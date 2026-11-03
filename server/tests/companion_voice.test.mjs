import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {loadVoiceScripts,VOICE_SCRIPTS} from '../../companion-ui/src/voiceLoader.mjs';
const root=fileURLToPath(new URL('../hidagent/static/',import.meta.url));
const voiceJs=readFileSync(root+'voice.js','utf8');
// Every element id voice.js looks up, via el('…') or document.getElementById('…').
const voiceIds=[...new Set([...voiceJs.matchAll(/(?:\bel|getElementById)\('([\w-]+)'\)/g)].map(m=>m[1]))];
const page=readFileSync(root+'companion/index.html','utf8');
const bundlePath=page.match(/src="\/static\/companion\/(assets\/index-[\w-]+\.js)"/)[1];
const bundle=readFileSync(root+'companion/'+bundlePath,'utf8');
test('voice.js id list is what the companion must render',()=>{
 for(const id of ['voice-start','voice-end','voice-mute','voice-status','voice-transcript','voice-camera-start','estop','zoom'])assert.ok(voiceIds.includes(id),id);
});
test('companion bundle renders every element id voice.js binds',()=>{
 for(const id of voiceIds)assert.ok(bundle.includes(`id:"${id}"`),`missing id ${id}`);
});
test('companion bundle references each voice script exactly once and voice-intents first',()=>{
 assert.equal(bundle.split('/static/voice.js').length-1,1);
 assert.equal(bundle.split('/static/voice-intents.js').length-1,1);
 assert.ok(bundle.indexOf('/static/voice-intents.js')<bundle.indexOf('/static/voice.js'));
 assert.ok(!page.includes('voice.js'));
});
test('companion bundle keeps the attribution footer',()=>{
 assert.match(bundle,/Built with assistant-ui \+ Hermes memory · ESP32 iPhone Agent by Henry Yuan/);
});
function fakeDom(){
 const appended=[];
 const doc={body:{append(s){appended.push(s);queueMicrotask(()=>s.onload());}},createElement:()=>({dataset:{}}),
  querySelector:sel=>appended.find(s=>sel.includes(`"${s.dataset.miloVoice}"`))||null};
 return {doc,appended};
}
test('loader injects voice-intents then voice.js once, even when called repeatedly',async()=>{
 const {doc,appended}=fakeDom(),win={};
 const a=loadVoiceScripts(doc,win),b=loadVoiceScripts(doc,win);
 assert.equal(a,b);
 await a; await loadVoiceScripts(doc,win);
 assert.deepEqual(appended.map(s=>s.dataset.miloVoice),VOICE_SCRIPTS);
 assert.deepEqual(VOICE_SCRIPTS,['/static/voice-intents.js','/static/voice.js']);
});
test('loader skips scripts already present in the document',async()=>{
 const {doc,appended}=fakeDom();
 await loadVoiceScripts(doc,{});
 await loadVoiceScripts(doc,{}); // fresh window flag, same document
 assert.equal(appended.length,2);
});
