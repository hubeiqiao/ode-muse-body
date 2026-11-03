"""Conservative public app searches over accessibility trees, no model calls.

Bundle identifiers below are candidates, not claims of installed app discovery.
A live run checks actual foreground bundle identity before interacting. Unknown
UI, inaccessible fields and weak result evidence fail closed without fallback.
"""
import asyncio
import re
import time

from .wda_driver import WDAError

APPS = {
    'youtube': {'bundle_id': 'com.google.ios.youtube'},
    'tiktok': {'bundle_id': 'com.zhiliaoapp.musically'},
    'xiaohongshu': {'bundle_id': 'com.xingin.discover'},
    'instagram': {'bundle_id': 'com.burbn.instagram'},
    'linkedin': {'bundle_id': 'com.linkedin.LinkedIn'},
    'x': {'bundle_id': 'com.atebits.Tweetie2'},
    'reddit': {'bundle_id': 'com.reddit.Reddit'},
    'app_store': {'bundle_id': 'com.apple.AppStore'},
    'google_maps': {'bundle_id': 'com.google.Maps'},
    'douyin': {'bundle_id': 'com.ss.iphone.ugc.Aweme'},
    'bilibili': {'bundle_id': 'tv.danmaku.bilianim'},
}
SEARCH_LABELS = ('Search', '搜索', '搜寻', '搜尋', 'Search YouTube', 'Search Reddit',
                 'Search LinkedIn', 'Search here', '搜索地点', '搜索笔记')
FIELD_TYPES = {'XCUIElementTypeSearchField', 'XCUIElementTypeTextField'}
RESULT_TYPES = {'XCUIElementTypeCell', 'XCUIElementTypeLink'}
BLOCKED_HEADINGS = {'direct messages', 'messages', 'inbox', 'new message', 'compose',
                    'sign in', 'log in', 'login', 'password', '私信', '收件箱', '登录', '密码'}


def yes(value):
    return value is True or value in ('true', '1', 1)


def text(node):
    return ' '.join(str(node.get(k, '')) for k in ('label', 'name', 'value')).strip()


def editable(nodes):
    return [n for n in nodes if n.get('type') in FIELD_TYPES and yes(n.get('visible'))
            and yes(n.get('enabled')) and any(label.casefold() == str(n.get(k, '')).casefold()
                                             for label in SEARCH_LABELS for k in ('label', 'name'))]


async def unique(wda, predicate):
    found = await wda.find(predicate=predicate)
    if len(found) != 1:
        raise WDAError('Search control is absent or ambiguous; no guessed tap')
    return found[0]


async def public_tree(wda):
    tree = await wda.source()
    for node in tree['nodes']:
        if node.get('visible') in (False, 'false', '0'):
            continue
        if node.get('type') == 'XCUIElementTypeSecureTextField' or (
                node.get('type') == 'XCUIElementTypeStaticText' and any(
                    str(node.get(key, '')).strip().casefold() in BLOCKED_HEADINGS for key in ('label', 'name'))):
            raise WDAError('Private or login context detected; public search refused')
    return tree


async def foreground(wda, bundle):
    info = await wda.active_app()
    if not isinstance(info, dict) or info.get('bundleId') != bundle:
        raise WDAError('Actual foreground bundle does not match the intended public app')


async def focused(wda, node, element):
    # Some iOS trees omit the tvOS-focused attribute. Use the active-element
    # identity in that case, never treat attribute absence as focus proof.
    if 'focused' in node:
        return yes(node['focused'])
    return await wda.active_element() == element


def verified_results(tree, query):
    nodes = tree['nodes']
    # Exclude editor descendants, keyboard, hidden nodes and the query echo.
    excluded = set()
    public = []
    for index, node in enumerate(nodes):
        if (node.get('type') in FIELD_TYPES | {'XCUIElementTypeKeyboard'}
                or node.get('parent') in excluded or node.get('visible') in (False, 'false', '0')):
            excluded.add(index)
        elif node.get('type') != 'XCUIElementTypeApplication':
            public.append(node)
    normalize = lambda s: re.sub(r'\s+', ' ', s).strip().casefold()
    q = normalize(query)
    # Require the complete query in a non-editable results announcement.
    announcements = [text(n) for n in public if n.get('type') in
                     {'XCUIElementTypeStaticText', 'XCUIElementTypeOther'} and q in normalize(text(n))
                     and any(mark in normalize(text(n)) for mark in ('results', '搜索结果', '搜尋結果', '相关结果'))]
    cards = [n for n in public if n.get('type') in RESULT_TYPES and text(n)]
    return bool(announcements and cards)


async def search(wda, app, query, *, wait_s=12):
    from .app_tools import SearchArgs
    if app not in APPS or not 0 < wait_s <= 30:
        raise WDAError('Unsupported public app or wait bound')
    query = SearchArgs(query=query).query
    started = time.perf_counter()
    status = await wda.status()
    if not isinstance(status, dict) or status.get('ready') is not True:
        raise WDAError('WDA is not ready')
    if not getattr(wda, 'session_id', None):
        await wda.start_session()
    bundle = APPS[app]['bundle_id']
    await wda.activate(bundle)
    await foreground(wda, bundle)
    tree = await public_tree(wda)
    if not editable(tree['nodes']):
        labels = ' OR '.join('label == ' + repr(label) for label in SEARCH_LABELS)
        entry = await unique(wda, f'type == "XCUIElementTypeButton" AND visible == 1 AND enabled == 1 AND ({labels})')
        await wda.tap(entry)
        tree = await public_tree(wda)
    fields = editable(tree['nodes'])
    if len(fields) != 1:
        raise WDAError('No unique app-owned public search editor; no input sent')
    field = fields[0]
    label = field.get('label') or field.get('name')
    element = await unique(wda, f'type == "{field["type"]}" AND label == {label!r} AND visible == 1 AND enabled == 1')
    await wda.tap(element)
    await foreground(wda, bundle)
    focus = editable((await public_tree(wda))['nodes'])
    if len(focus) != 1 or not await focused(wda, focus[0], element):
        raise WDAError('Search focus is not proven by accessibility; no typing')
    await wda.clear(element)
    cleared = editable((await public_tree(wda))['nodes'])
    if len(cleared) != 1 or cleared[0].get('value') not in ('', None):
        raise WDAError('Search field was not cleared; no typing')
    await wda.type_focused(query)
    echoed = editable((await public_tree(wda))['nodes'])
    if len(echoed) != 1 or echoed[0].get('value') != query or not await focused(wda, echoed[0], element):
        raise WDAError('Query receipt not proven; never resend input', delivery_uncertain=True)
    await foreground(wda, bundle)
    await wda.type_focused('\n')
    deadline = time.monotonic() + wait_s
    while True:
        await foreground(wda, bundle)
        tree = await public_tree(wda)
        if verified_results(tree, query):
            return {'driver': 'wda', 'app': app, 'bundle_id': bundle, 'query': query,
                    'verified': True, 'verification': 'accessibility_tree', 'tree': tree,
                    'model_calls': 0, 'elapsed_s': round(time.perf_counter() - started, 6),
                    'input_was_sent': True, 'never_retry_typing': False}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WDAError('No query-specific loaded results in tree; do not resend query')
        await asyncio.sleep(min(.25, remaining))
