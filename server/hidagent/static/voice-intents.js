/* Stop detection runs locally on input transcription, before tool selection. */
(function(root) {
  function isStopIntent(value) {
    let text = value.trim().toLowerCase();
    if (/^停[。！!]*$/.test(text)) return true;
    if (/^(?:please )?(?:work (?:towards?|on) (?:this |a )?goal|start (?:this |a )?goal)[\s:：,.]/i.test(text)) return false;
    if (/^(?:(?:please|请|帮我|请帮我|你帮我)\s*)?(?:remember|type|write|输入|写下|写一句|写一条|发一条|朗读)/i.test(text)) return false;
    text = text.replace(/\b(?:(?:don['’]t|do\s+not|doesn['’]t|does\s+not)(?:\s+(?:need|have)\s+to)?|no\s+need\s+to)\s+stop\b|\bnever\s+mind\s+stopping\b|(?:不要|不用|不需要|没必要|不必)停(?:止|下来|一下)?|别停|不停/g, '');
    if (/^(?:(?:please|请|帮我|请帮我)\s*)?(?:(?:先别|先不要|先不|不要|别再)导航|(?:don't|do not)\s+navigate|cancel\s+navigation)/i.test(text)) return true;
    return /不(?:用|要|再)?[划滑画]了|别(?:再)?[划滑画]|不要(?:再)?[划滑画]|停止(?:操作|滑动|滚动)?|停下来|停一下|先停|\bstop\b|no more scroll|enough scrolling/i.test(text);
  }
  function isActionIntent(text) {
    if (/^(?:Joe Speaking 各平台运营情况怎么样|拍|倒计时三秒再拍|切前置|切后置|上次.{0,80}(?:产品|帖子).{0,40}|发送|post it|send it|yes post)[.!。！]*$/i.test(text.trim())) return true;
    if (isStopIntent(text)) return false;
    text = text.trim().replace(/^(?:(?:(?:um|no|yeah|okay|i mean|so|well)\b|嗯|那个|不是|那(?:就)?|然后|再|请帮我|帮我|请|你)\s*[，,]?\s*)+/i, '');
    // Match whole English action words after conversational fillers, not prefixes like 'good'.
    return /\b(?:work (?:towards?|on) (?:this |a )?goal|start (?:this |a )?goal|draft|prepare|remember|read (?:the )?current (?:inventory|page)|create a new (?:Notes )?note|do (?:the )?exploration|surprise me|(?:do )?whatever (?:else )?(?:you )?like|do something by yourself|do whatever (?:else )?(?:you )?like|carry on|take (?:a |the )?(?:photo|picture)|flip (?:the )?camera|close (?:the )?camera|explore|investigate|choose|open|launch|calculate|compute|scroll|swipe|tap|click|type|write|go|find|search|continue|resume)\b|回(?:到)?(?:地图|天气|主屏|桌面|设置|备忘录)|看看|查看|查一下|查查|根据(?:这个|当前)(?:库存|页面)|拍照|翻转相机|关闭相机|探索|新建|创建|随便做点什么|打开|计算|算一下|继续|向[上下左右]|往[上下左右]|[划滑]|输入|搜索|返回|切换|找到|^(?:自己|给你|点)/i.test(text.trim());
  }
  const api = {isStopIntent, isActionIntent};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PhoneVoiceIntents = api;
})(typeof window === 'undefined' ? {} : window);
