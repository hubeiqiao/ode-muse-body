"""Small deterministic control intents; never interpret dictated body text."""
import re


def is_stop_intent(text):
    text = text.strip().lower()
    if re.fullmatch(r'停[。！!]*', text): return True
    if re.match(r'^(?:please )?(?:work (?:towards?|on) (?:this |a )?goal|start (?:this |a )?goal)[\s:：,.]',text):return False
    if re.match(r'^(?:(?:please|请|帮我|请帮我|你帮我)\s*)?(?:remember|type|write|输入|写下|写一句|写一条|发一条|朗读)', text):
        return False
    text = re.sub(r"\b(?:(?:don['’]t|do\s+not|doesn['’]t|does\s+not)(?:\s+(?:need|have)\s+to)?|no\s+need\s+to)\s+stop\b|\bnever\s+mind\s+stopping\b|(?:不要|不用|不需要|没必要|不必)停(?:止|下来|一下)?|别停|不停", '', text)
    if re.search(r"^(?:(?:please|请|帮我|请帮我)\s*)?(?:(?:先别|先不要|先不|不要|别再)导航|(?:don't|do not)\s+navigate|cancel\s+navigation)", text):
        return True
    return bool(re.search(r'不(?:用|要|再)?[划滑画]了|别(?:再)?[划滑画]|不要(?:再)?[划滑画]|停止(?:操作|滑动|滚动)?|停下来|停一下|先停|\bstop\b|no more scroll|enough scrolling', text))


def is_action_intent(text):
    if re.fullmatch(r'(?:Joe Speaking 各平台运营情况怎么样|拍|倒计时三秒再拍|切前置|切后置|上次.{0,80}(?:产品|帖子).{0,40}|发送|post it|send it|yes post)[.!。！]*', text.strip(), re.I):
        return True
    if is_stop_intent(text):
        return False
    text = re.sub(r'^(?:(?:(?:um|no|yeah|okay|i mean|so|well)\b|嗯|那个|不是|那(?:就)?|然后|再|请帮我|帮我|请|你)\s*[，,]?\s*)+', '', text.strip(), flags=re.I)
    # Keep whole-word action matching aligned with static/voice-intents.js.
    return bool(re.search(
        r'\b(?:work (?:towards?|on) (?:this |a )?goal|start (?:this |a )?goal|draft|prepare|remember|read (?:the )?current (?:inventory|page)|create a new (?:Notes )?note|do (?:the )?exploration|surprise me|(?:do )?whatever (?:else )?(?:you )?like|do something by yourself|do whatever (?:else )?(?:you )?like|carry on|take (?:a |the )?(?:photo|picture)|flip (?:the )?camera|close (?:the )?camera|explore|investigate|choose|open|launch|calculate|compute|scroll|swipe|tap|click|type|write|go|find|search|continue|resume)\b|回(?:到)?(?:地图|天气|主屏|桌面|设置|备忘录)|看看|查看|查一下|查查|根据(?:这个|当前)(?:库存|页面)|拍照|翻转相机|关闭相机|探索|新建|创建|随便做点什么|打开|计算|算一下|继续|向[上下左右]|往[上下左右]|[划滑]|输入|搜索|返回|切换|找到|^(?:自己|给你|点)', text.strip(), re.I))


def scroll_request(text):
    """A plain scroll means once. Explicit continuous requests have a finite cap."""
    text = text.strip().lower()
    # These bounded suffixes constrain one gesture; they do not request an
    # open-ended navigation task. Strip only this explicit safe grammar.
    text = re.sub(r'(?:,\s*|\s+)then stop(?:\.\s*do not tap links or buttons)?[.!。！]*$', '', text)
    text = text.rstrip('.!。！')
    text = re.sub(r'\s+on the current (?:safari |browser )?page$', '', text)
    if is_stop_intent(text):
        return None
    m = re.fullmatch(r'(?:please\s+)?(?:(keep|continue)\s+)?(?:scroll(?:ing)?|swipe)\s+(down|up|left|right)(?:\s+(?:the\s+)?(?:feed|page|list|screen))?(?:\s+(once|one time|one screen|a bit|a little|a little bit|further|continuously|until i say stop))?', text)
    if m:
        return m[2], (3 if m[1]=='keep' or m[3] in ('continuously','until i say stop') else 1)
    m = re.fullmatch(r'(?:请|帮我)?(一直|继续)?(?:往|向)?([上下左右])(?:滑动|划动|滚动|翻|滑|划)(?:一下|一次|一屏)?', text)
    if m:
        return {'下':'down','上':'up','左':'left','右':'right'}[m[2]], (3 if m[1]=='一直' else 1)
    return None
