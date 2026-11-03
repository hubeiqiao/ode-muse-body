"""Source-bound local review, exact account UI and completion-notice contract.
No posting, Bot messages, clipboard access or model-invented completion flags.
"""
import hashlib,os,time
from .decision import DecisionUnavailable
from .app_controls import public_search_control

def identity():
    # Owner-chosen name (identity.ai_name_info): owner setting, then env AI_NAME, then the default.
    from .identity import ai_name_info
    return ai_name_info()

def verify_visible_account(agent,active):
    active();p=agent.phone;t=p.target_info();frame=agent.screen.frame
    if agent.running or not agent.screen.live or not p.device.connected or not p.calibrated:raise DecisionUnavailable('A live idle calibrated device is required')
    self_profile=True
    if not public_search_control(frame,'X',t,control='example_creator_self_profile'):
        # Landscape only: the inline reply composer prints the actual posting
        # account (name + handle). Exact registered pixels, never avatar-only.
        if t.get('orientation')!='landscape' or not public_search_control(frame,'X',t,control='example_creator_reply_composer'):
            raise DecisionUnavailable('Actual @example_creator self-profile UI is not visible; no account inferred from avatar')
        self_profile=False
    active()
    return {'account':'@example_creator','account_verified':True,'self_profile':self_profile,'source_kind':'exact_registered_self_profile_ui' if self_profile else 'exact_registered_reply_composer_account_ui','frame_sha256':hashlib.sha256(frame).hexdigest(),'observed_at':time.time(),'link_generation':p.device.links,'calibration_key':t.get('calibration_key'),'published':False,'scope':'Exact current self-profile header/handle/Edit Profile/Share Profile pixels. Historical immediately after this observation, not permission or proof of publication.'}

def completed_call(tools,key,name):
    row=tools.calls.get(key)
    if not row or row.get('name')!=name or row.get('status')!='completed' or not row['job'].done() or row['job'].cancelled():raise DecisionUnavailable('An exact completed source receipt in this process is required')
    return row

def prepare_reply(tools,source_id,account_id):
    source=completed_call(tools,source_id,'grok.read_action')['result'];account=completed_call(tools,account_id,'x.verify_account')['result']
    if not source.get('verification_passed') or source.get('workspace')!='Example Action Workspace' or source.get('source_kind')!='verified_dedicated_action_snapshot':raise DecisionUnavailable('Dedicated source is not verified')
    if not account.get('account_verified') or account.get('account')!='@example_creator':raise DecisionUnavailable('Required X account not verified')
    if not 0<=time.time()-account.get('observed_at',0)<=120:raise DecisionUnavailable('Account observation expired; recheck actual self-profile')
    text=source['reply_text']
    length=None
    if text is not None:
        from .reply_length import measure_reply
        length=measure_reply(text)
        if not length['valid']:raise DecisionUnavailable('Prepared reply exceeds X weighted limit or contains invalid text; remaining='+str(length['remaining'])+'. No silent truncation or input.')
    if text is not None and '\u2014' in text:raise DecisionUnavailable('Source contains an em dash; do not silently rewrite the copied reply')
    return {'body':text,'weighted_length':length,'source_read_request_id':source_id,'account_read_request_id':account_id,'card_id':source['card_id'],'source_frame_sha256':source['frame_sha256'],'target_handle':source['target_handle'],'target_post_url':source['target_post_url'],'expected_account':'@example_creator','ai_identity':identity(),'source_verbatim':False,'requires_native_copy':True,'byte_exact_copy_verified':False,'target_verified':False,'requires_target_context_check':True,'status':'needs_native_copy' if text is None else 'needs_target_grounding','published':False,'acknowledged':False,'scope':'Local visual-transcription review only, not outgoing copy. No X typing, publication or Grok acknowledgement. The actual target post, account and reply must be checked again before sending.'}

def completion_notice(expected_items,publication_receipts,ai_name=None):
    # Inputs are INTERNAL server-verified receipts, never an API bool from a model.
    if not expected_items or len(expected_items)>8 or len(set(expected_items))!=len(expected_items):raise DecisionUnavailable('An explicit bounded unique batch is required')
    proven={}
    for receipt in publication_receipts:
        item=receipt.get('item_id')
        if (item in proven or item not in expected_items or receipt.get('state')!='visibly_verified_published'
            or receipt.get('account')!='@example_creator' or receipt.get('delivery_uncertain') or not receipt.get('reply_permalink')
            or not receipt.get('verification_frame_sha256')):raise DecisionUnavailable('Batch has unverified, duplicate or uncertain publication; no completion notice')
        proven[item]=receipt
    if set(proven)!=set(expected_items):raise DecisionUnavailable('Not every batch item is visibly published; no completion notice')
    if ai_name is None:ai_name=identity()['name']
    if not isinstance(ai_name,str) or not ai_name.strip() or len(ai_name)>40 or any(ord(c)<32 for c in ai_name):raise DecisionUnavailable('Human-configured AI name is required')
    return f'All {len(expected_items)} comments in this batch have been posted. This is {ai_name}, the AI controlling this iPad.'
