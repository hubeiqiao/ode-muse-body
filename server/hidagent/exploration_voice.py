"""Recognize explicit independent exploration, excluding literal dictation."""
import re

def is_public_device_request(text):
    """Concrete public-content targets belong to the normal visual executor."""
    if not isinstance(text, str):return False
    return bool(re.search(r'\b(?:find|search|browse|explore|show|read|view|look for)\b', text, re.I)
        and re.search(r'\b(?:X|Twitter|YouTube|Reddit|Safari|Threads|Instagram|TikTok|rednote)\b', text, re.I))

def is_exploration_intent(text):
    if not isinstance(text,str):return False
    # Structured tool goals (e.g. a public search for 'space exploration') are not owner exploration requests.
    if text.lstrip().startswith('APP_PUBLIC_SEARCH '):return False
    if re.match(r'^(?:please\s+|请)?(?:type|write|输入|写下|朗读)',text.strip(),re.I):return False
    if re.match(r'^(?:please\s+)?(?:open|打开).{0,60}(?:\btype\b|\bwrite\b|输入|写入|写下)',text.strip(),re.I):return False
    if is_public_device_request(text):return False
    return bool(re.search(r'探索|自己.{0,8}(?:选题|找|看看|发现)|给.{0,5}(?:AI|你).{0,6}手机|\bexplor(?:e|ation)\b|\bchoose.{0,20}(?:own topic|topic yourself)\b|\bbring back.{0,30}discover|\bdo something by yourself\b|\bsurprise me\b|\b(?:do )?whatever (?:else )?(?:you )?like\b|随便做点什么',text,re.I))


def device_request_tool(text):
    if is_exploration_intent(text):return 'phone_explore'
    if is_public_device_request(text) or re.match(r'^(?:please )?(?:open|type|tap|click|scroll|swipe|go (?:home|back))\b',text,re.I):
        return 'phone_command'
    return None
