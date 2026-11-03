// Browser wire and readback gate with fake WebSocket, audio, fetch and clock.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let source = fs.readFileSync(require('node:path').join(__dirname, '../hidagent/static/voice.js'), 'utf8');
const marker = '  async function tool(call, generation, sid) {';
assert(source.includes(marker));
source = source.replace(marker, `  window.demoTest = {
  input: handleInput,
  state: () => ({ownerInputTurn, demoReadback}),
  receipt: receipt => trackDemoReadback(receipt),
  output: (text, done, heard) => { if (demoReadback) {demoReadback.text += text; demoReadback.turnDone = done; demoReadback.heard = heard;} maybeFinishDemoReadback(); },
  interrupt: clearAudio,
  init: () => {session='offline-session'; active=true;},
  pendingAudio: n => {playing.clear(); for(let i=0;i<n;i++) playing.add({stop(){}});},
  doneAudio: () => {playing.clear(); maybeFinishDemoReadback();}
};\n${marker}`);
const els = new Map(), requests=[];
const el = id => { if (!els.has(id)) els.set(id, {addEventListener(){}, disabled:false, hidden:false, dataset:{}});return els.get(id);};
const context = {window:{PhoneVoiceIntents:{isStopIntent:()=>false,isActionIntent:()=>false},addEventListener(){}},
 document:{documentElement:{lang:'en'},getElementById:el,addEventListener(){},body:{dataset:{}}},
 fetch:async(url, opts)=>{requests.push({url, body:JSON.parse(opts.body)});return {ok:true,json:async()=>({ok:true})};},
 setTimeout(){},clearTimeout(){},Date,JSON,console};
vm.runInNewContext(source, context);
const t=context.window.demoTest;
(async()=>{
 t.init(); t.input('Draft a reply');const turn=t.state().ownerInputTurn;
 t.input(' to that post');assert.equal(t.state().ownerInputTurn,turn,'transcription chunks share one human turn');
 const receipt={status:'awaiting_confirmation',draft_id:'d1',readback:'Exact reply'};
 t.receipt(receipt);t.pendingAudio(1);t.output('Exact reply',true,true);
 assert.equal(requests.length,0,'queued audio has not finished');
 t.doneAudio();await new Promise(setImmediate);
 assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/voice/demo-readback');
 assert.equal(requests[0].body.text,'Exact reply');
 t.receipt({...receipt,draft_id:'d2'});t.pendingAudio(1);t.output('Exact reply',true,true);t.interrupt();
 t.doneAudio();await new Promise(setImmediate);assert.equal(requests.length,1,'interrupted audio never acknowledges');
 t.receipt({...receipt,draft_id:'d3'});t.output('Different reply',true,true);await new Promise(setImmediate);
 assert.equal(requests.length,1,'different spoken text never acknowledges');
 console.log('ipad demo browser readback and owner turn checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
