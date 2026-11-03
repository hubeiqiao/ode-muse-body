"""One-shot, owner/session-bound camera follow-up. Never replay transcript tools."""
import asyncio
import re
import time
import unicodedata

from .full_screen_state import identity

TTL_S = 120
GREETING = re.compile(r'\s*(?:hello|hi|hey|hello there|are you there|你好|喂)[?!.。！？，\s]*', re.I)
PUBLICATION = re.compile(r'\b(?:post(?:ed|ing)?|publish(?:ed|ing)?|shar(?:e|ed|ing)|upload(?:ed|ing)?|tweet(?:ed|ing)?)\b|发|传', re.I)
DEFAULT_CAPTION = 'Taken by Ode, my AI living on an old iPad.'


def photo_post_request(text):
    text = unicodedata.normalize('NFKC', text)
    # Unicode word boundaries treat Chinese next to X as one word. Voice also
    # transcribes the app name as "ex"; neither may fall into general planning.
    return bool(PUBLICATION.search(text) and (re.search(
        r'(?<![A-Za-z0-9_])(?:x|twitter|ex)(?![A-Za-z0-9_])|推特|\btweet(?:ed|ing)?\b', text, re.I)))


def direct_photo_post_request(text):
    return photo_post_request(text) and bool(re.search(r'\b(?:photo|picture|image)\b|照片|图片|这张', text, re.I))


def caption_draft(request, scene):
    literal = re.search(r"(?:caption(?: it| the photo)?(?: with| as)?|type|write|配文|文字)\s*[:：]?\s*[\"“'‘](.+?)[\"”'’]", request, re.I)
    if literal: return literal[1]
    tail = re.search(r'(?:with (?:a |the )?caption|(?:and |then )?(?:type|write(?!\s+that\b))|配文(?:为)?)\s*[:：]?\s+(.+)$', request, re.I)
    if tail and not re.search(r'describ|scene|something|caption|描述|场景', tail[1], re.I):
        return tail[1].strip()
    spoken = re.search(r'\b(?:say(?:ing)?|tell them|write that)\b\s*[:：]?\s*(.+)$|说\s*[:：]?\s*(.+)$', request, re.I)
    if spoken:
        caption = (spoken[1] or spoken[2]).strip()
        # These camera instructions describe authorship rather than dictate a
        # literal caption. Keep the rewrite bounded to the owner's named intent.
        if re.fullmatch(r'(?:this was )?Joe (?:asked Ode|controlling Ode) to take (?:it|the photo|this photo)[.!]?', caption, re.I):
            return 'Joe asked Ode, his AI on an old iPad, to take this photo.'
        return caption
    # Camera-generated captions use one short ASCII sentence on the existing
    # HID type path. Owner literals above remain verbatim.
    scene = ' '.join(scene.split()).translate(str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"', '—': '-', '–': '-'}))
    scene = unicodedata.normalize('NFKD', scene)
    scene = ''.join(c for c in scene if not unicodedata.combining(c))
    if not scene or not scene.isascii(): return DEFAULT_CAPTION
    return re.split(r'(?<=[.!?])\s+', scene, maxsplit=1)[0][:280]


async def route_photo_draft(bridge, call, session, *, scene=''):
    caption = caption_draft(call.utterance, scene)
    if not bridge.demos or not caption:
        return {'status': 'not_ready', 'terminal': True,
                'reason': 'Photo draft needs iPad demos. Never use the general agent or replay take_photo.'}
    return await bridge.call_demo(call.model_copy(update={'name': 'ipad_demo',
        'args': {'action': 'x_photo_draft', 'text': caption, 'account': '@example_creator'}}), session)


class CameraHandoff:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.pending = {}

    def offer(self, call, session, state):
        if session.get('camera_handoff_offered'): return False
        text = call.utterance.strip()
        if not text or GREETING.fullmatch(text): return False
        if re.fullmatch(r'(?:please )?(?:return|switch|go) (?:back )?to (?:normal |voice |device )?mode[.!]?', text, re.I):
            return False
        self.pending = {sid: p for sid, p in self.pending.items() if p['expires'] > self.clock()}
        session['camera_handoff_offered'] = True
        scene = call.args.get('scene_description') or call.scene_description
        scene = ' '.join(scene.split())[:280] if isinstance(scene, str) else ''
        self.pending[call.session] = dict(owner=call.session, request=text, scene=scene,
            photo=session.get('last_camera_photo') == 'photo_taken',
            device=(identity(state.get('target') or {}), (state.get('target') or {}).get('device')), expires=self.clock()+TTL_S)
        return True

    def claim(self, source, session, state):
        if not isinstance(source, str): return None
        pending = self.pending.get(source)
        if (not pending or pending['expires'] <= self.clock()
                or pending['device'] != (identity(state.get('target') or {}), (state.get('target') or {}).get('device'))):
            self.pending.pop(source, None)
            return None
        self.pending.pop(source)
        session['pending_camera_intent'] = pending
        session['camera_photo_context'] = pending
        session['camera_followup'] = True
        return {'id': 'camera-handoff', 'name': 'resume_camera_intent', 'args': {}}

    def cancel(self, sid, session):
        self.pending.pop(sid, None)
        session.pop('pending_camera_intent', None)
        session.pop('camera_photo_context', None)

    async def resume(self, bridge, call, session):
        # Reserve/consume synchronously before the first await. A dropped HTTP
        # receipt or an invented second tool ID cannot start this action twice.
        old = session.get('camera_intent_task')
        if old is not None: return await asyncio.shield(old)
        pending = session.pop('pending_camera_intent', None)
        if (not pending or pending['expires'] <= self.clock() or session.get('paused')
                or pending['device'] != (identity(bridge.state().get('target') or {}), (bridge.state().get('target') or {}).get('device'))):
            return {'status': 'rejected', 'terminal': True, 'reason': 'No current owner camera follow-up'}

        async def execute():
            if photo_post_request(pending['request']):
                if not pending['photo']:
                    return {'status': 'not_ready', 'terminal': True,
                            'reason': 'The camera photo was not confirmed. Do not take another or attach an older photo.'}
                from .live_voice import ToolCall
                # Reuse normal validation, ownership, cancellation and demo deduplication.
                return await route_photo_draft(bridge, ToolCall(session=call.session, id=call.id,
                    name='ipad_demo', owner_turn=1, utterance=pending['request']), session, scene=pending['scene'])
            if pending['photo'] and PUBLICATION.search(pending['request']):
                return {'status': 'not_ready', 'terminal': True,
                        'reason': 'Confirm X as the destination and a caption for this saved photo. Never use general planning to find or publish another image.'}
            # Other follow-ups carry only the current owner request, never past tools.
            return await bridge.execute('phone_command', {'goal': pending['request']}, call.session, call.id)
        task = asyncio.create_task(execute())
        session['camera_intent_task'] = task
        return await asyncio.shield(task)
