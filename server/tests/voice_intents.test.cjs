const {test} = require('node:test');
const assert = require('node:assert/strict');
const {isStopIntent, isActionIntent} = require('../hidagent/static/voice-intents.js');
const recovery = [
  "um could you yeah could you do something by yourself to find some interesting and and just tell me yeah this is your iPad you can do whatever else you like.",
  "No, you you can do the exploration now.",
  "No, I mean you can do exploration. You don't need to stop.",
];
const negations = ["don't stop", "do not stop", "doesn't need to stop", "don't need to stop", "don't have to stop", "You don't need to stop", "you don't have to stop", "no need to stop", "never mind stopping", "不用停", "不需要停", "没必要停", "不必停"];
for (const text of negations) test(`negated stop: ${text}`, () => assert.equal(isStopIntent(text), false));
for (const text of ['stop', 'stop stop stop', 'okay stop', '停', '停下来', 'no need to stop, but stop now', '不用停？还是停下来吧']) {
  test(`real stop: ${text}`, () => { assert.equal(isStopIntent(text), true); assert.equal(isActionIntent(text), false); });
}
for (const text of [...recovery, 'you can do the exploration', 'do something by yourself', 'do whatever you like', 'explore', 'go ahead', 'carry on', 'you can continue', '去探索', '你自己玩', '随便做点什么', ...['um', 'No,', 'yeah', 'okay', 'I mean', 'so', 'well', '嗯', '那个', '不是'].map(s => `${s} explore`)]) {
  test(`fresh action: ${text}`, () => { assert.equal(isStopIntent(text), false); assert.equal(isActionIntent(text), true); });
}
for (const text of ['thanks', 'how are you', 'good morning', '今天天气有点冷', 'This inventory is interesting', '那就算了']) {
  test(`chit-chat: ${text}`, () => assert.equal(isActionIntent(text), false));
}

for(const text of ['take a photo','flip the camera','close the camera','拍照','翻转相机','关闭相机']) {
 test(`fresh camera action: ${text}`,()=>assert.equal(isActionIntent(text),true));
}
