const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'../hidagent/static');
class Element {
 constructor(){this.children=[];this.dataset={};this.handlers={};this.disabled=false;this.hidden=false;this.open=false;this.textContent='';}
 addEventListener(k,f){this.handlers[k]=f;}
 append(...nodes){for(const node of nodes)node.parent=this;this.children.push(...nodes);}
 get lastElementChild(){return this.children.at(-1);}
 get firstElementChild(){return this.children[0];}
 get lastChild(){return this.children.at(-1);}
 remove(){this.parent.children.splice(this.parent.children.indexOf(this),1);}
}
const els=new Map();const el=id=>{if(!els.has(id))els.set(id,new Element());return els.get(id)};
let socket,resolveOld,resolveResume;let holdResume=false;const requests=[];
class WS {
 static OPEN=1;
 constructor(){socket=this;this.readyState=1;this.sent=[];}
 send(s){this.sent.push(JSON.parse(s))} close(){this.readyState=3;}
}
class Audio {
 constructor(){this.state='running';this.sampleRate=16000;this.audioWorklet={addModule:async()=>{}};this.destination={};}
 async resume(){} async close(){this.state='closed'}
 createMediaStreamSource(){return {connect(){},disconnect(){}}}
 createGain(){return {gain:{value:0},connect(){}}}
}
const stream={getTracks:()=>[{stop(){}}],getAudioTracks:()=>[{enabled:true}]};
const response=data=>({ok:true,json:async()=>data});
async function fetch(url,opts={}) {
 const body=opts.body?JSON.parse(opts.body):{};requests.push({url,body});
 if(url==='/api/voice/status')return response({configured:true});
 if(url==='/api/voice/session')return response({session:'s'.repeat(32),token:'auth_tokens/test',setup:{}});
 if(url==='/api/voice/interrupt')return response({status:'stopped',paused:true});
 if(url==='/api/voice/resume'){if(holdResume)return new Promise(r=>{resolveResume=()=>r(response({status:'ready',paused:false}))});return response({status:'ready',paused:false});}
 if(url==='/api/voice/tool'&&body.id.startsWith('old'))return new Promise(r=>{resolveOld=()=>r(response({status:'stopped',paused:true,goal:'scroll down',task_id:'old-task'}))});
 if(url==='/api/voice/tool')return response({status:'model_reported_done',goal:'Open Calculator',task_id:'new-task'});
 return response({ok:true});
}
const context={document:{documentElement:{lang:'zh-CN'},body:{dataset:{}},getElementById:el,createElement:()=>new Element(),createTextNode:text=>({textContent:text}),addEventListener(){}},
 window:{PhoneVoiceIntents:require(path.join(root,'voice-intents.js')),addEventListener(){}},
 navigator:{mediaDevices:{getUserMedia:async()=>stream}},AudioContext:Audio,AudioWorkletNode:class{constructor(){this.port={}}connect(){}disconnect(){}},
 WebSocket:WS,fetch,Date,JSON,Promise,TextDecoder,ArrayBuffer,DataView,Uint8Array,setTimeout,clearTimeout,console};
vm.runInNewContext(fs.readFileSync(path.join(root,'voice.js'),'utf8'),context);
const tick=async()=>{for(let i=0;i<6;i++)await new Promise(setImmediate)};
const emit=async data=>{socket.onmessage({data:JSON.stringify(data)});await tick()};
(async()=>{
 await el('voice-start').handlers.click();socket.onopen();await emit({setupComplete:{}});
 await emit({toolCall:{functionCalls:[{id:'old',name:'phone_command',args:{goal:'scroll down'}}]}});
 assert(resolveOld,'phone command must be pending');
 await emit({serverContent:{inputTranscription:{text:'不'}}});
 await emit({serverContent:{inputTranscription:{text:'划了'}}});
 assert.equal(requests.filter(r=>r.url==='/api/voice/interrupt').length,1,'split Mandarin stop must directly interrupt');
 await emit({toolCallCancellation:{ids:['old']}});
 resolveOld();await tick();
 assert(!socket.sent.some(m=>m.toolResponse?.functionResponses?.some(r=>r.id==='old')),'cancelled call must never return a stale response');
 await emit({toolCall:{functionCalls:[{id:'late',name:'phone_command',args:{goal:'scroll down'}}]}});
 assert(!requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='late'),'late action must be latched out');
 await emit({toolCall:{functionCalls:[{id:'late-math',name:'phone_calculate',args:{left:24,right:18,operator:'multiply'}}]}});
 assert(!requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='late-math'),'late structured math must also be latched out');
 await emit({toolCall:{functionCalls:[{id:'late-restock',name:'phone_restock_note',args:{}}]}});
 assert(!requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='late-restock'),'late structured restock must also be latched out');
 await emit({serverContent:{outputTranscription:{text:'已停止'}}});
 await emit({serverContent:{inputTranscription:{text:'打开计算器'}}});
 assert.equal(requests.filter(r=>r.url==='/api/voice/resume').length,1);
 await emit({toolCall:{functionCalls:[{id:'new',name:'phone_command',args:{goal:'Open Calculator'}}]}});
 assert(requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='new'),'new user request can resume');
 await el('voice-end').handlers.click();
 // An old task's late stopped receipt must not latch a newly started session.
 await el('voice-start').handlers.click();socket.onopen();await emit({setupComplete:{}});
 await emit({toolCall:{functionCalls:[{id:'old-again',name:'phone_command',args:{goal:'scroll down'}}]}});
 const lateResolve=resolveOld;
 await el('voice-end').handlers.click();
 await el('voice-start').handlers.click();socket.onopen();await emit({setupComplete:{}});
 lateResolve();await tick();
 await emit({toolCall:{functionCalls:[{id:'fresh-session',name:'phone_command',args:{goal:'Open Calculator'}}]}});
 assert(requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='fresh-session'),'late old receipt cannot pause fresh session');
 await el('voice-end').handlers.click();
 await el('voice-start').handlers.click();socket.onopen();await emit({setupComplete:{}});
 await emit({toolCall:{functionCalls:[{id:'old-math',name:'phone_calculate',args:{left:24,right:18,operator:'multiply'}}]}});
 const finishMath=resolveOld,interruptCount=requests.filter(r=>r.url==='/api/voice/interrupt').length;
 await emit({toolCallCancellation:{ids:['old-math']}});
 assert.equal(requests.filter(r=>r.url==='/api/voice/interrupt').length,interruptCount+1,'structured math cancellation must physically interrupt');
 finishMath();await tick();
 await emit({serverContent:{turnComplete:true}});
 await emit({serverContent:{inputTranscription:{text:'计算24乘以18'}}});
 await emit({toolCall:{functionCalls:[{id:'fresh-math',name:'phone_calculate',args:{left:24,right:18,operator:'multiply'}}]}});
 assert(requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='fresh-math'),'new explicit math speech may resume');
 await el('voice-end').handlers.click();
 // Navigation correction: stop the old task even in a compound utterance.
 await el('voice-start').handlers.click();socket.onopen();await emit({setupComplete:{}});
 await emit({toolCall:{functionCalls:[{id:'old-navigation',name:'phone_command',args:{goal:'Open Maps'}}]}});
 const finishNavigation=resolveOld,navInterrupts=requests.filter(r=>r.url==='/api/voice/interrupt').length;
 await emit({serverContent:{inputTranscription:{text:'先别'}}});
 await emit({serverContent:{inputTranscription:{text:'导航，看看明天下午会不会下雨'}}});
 assert.equal(requests.filter(r=>r.url==='/api/voice/interrupt').length,navInterrupts+1,'navigation cancellation must stop the old physical task');
 await emit({toolCall:{functionCalls:[{id:'late-navigation',name:'phone_command',args:{goal:'Open Maps'}}]}});
 assert(!requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='late-navigation'),'old navigation must remain blocked');
 finishNavigation();await tick();
 await emit({serverContent:{outputTranscription:{text:'已停止'}}});
 await emit({serverContent:{inputTranscription:{text:'那回地图，换成公共交通看看'}}});
 await emit({toolCall:{functionCalls:[{id:'new-navigation',name:'phone_command',args:{goal:'Open Maps'}}]}});
 assert(requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='new-navigation'),'a fresh conversational navigation request can resume');
 await el('voice-end').handlers.click();
 await el('voice-start').handlers.click();socket.onopen();await emit({setupComplete:{}});
 await emit({serverContent:{inputTranscription:{text:'Work toward this goal: Find one public idea, then stop for review.'}}});
 const goalInterrupts=requests.filter(r=>r.url==='/api/voice/interrupt').length;
 await emit({toolCall:{functionCalls:[{id:'old-mission',name:'start_app_goal',args:{goal:'Find one public idea'}}]}});
 assert(requests.some(r=>r.url==='/api/voice/tool'&&r.body.id==='old-mission'&&r.body.utterance.startsWith('Work toward this goal:')),'goal forwards original explicit utterance');
 const finishMission=resolveOld;
 await emit({toolCallCancellation:{ids:['old-mission']}});
 assert.equal(requests.filter(r=>r.url==='/api/voice/interrupt').length,goalInterrupts+1,'goal cancellation reaches physical Stop');
 finishMission();await tick();await el('voice-end').handlers.click();
 // Real incident: each fresh utterance must resume before its tool can dispatch.
 const recovery = [
  "um could you yeah could you do something by yourself to find some interesting and and just tell me yeah this is your iPad you can do whatever else you like.",
  "No, you you can do the exploration now.",
  "No, I mean you can do exploration. You don't need to stop.",
 ];
 for (const [index, text] of recovery.entries()) {
  await el('voice-start').handlers.click();socket.onopen();await emit({setupComplete:{}});
  await emit({toolCall:{functionCalls:[{id:`old-recovery-${index}`,name:'phone_explore',args:{}}]}});
  const finishOld=resolveOld;
  await emit({serverContent:{inputTranscription:{text:'Okay, stop stop stop.'}}});
  await emit({toolCallCancellation:{ids:[`old-recovery-${index}`]}});
  await emit({serverContent:{turnComplete:true}});
  const resumeCount=requests.filter(r=>r.url==='/api/voice/resume').length;
  await emit({serverContent:{inputTranscription:{text:'thanks'}}});
  assert.equal(requests.filter(r=>r.url==='/api/voice/resume').length,resumeCount,'chit-chat cannot release stop');
  await emit({toolCall:{functionCalls:[{id:`blocked-${index}`,name:'phone_explore',args:{}}]}});
  const blocked=socket.sent.flatMap(m=>m.toolResponse?.functionResponses||[]).find(r=>r.id===`blocked-${index}`);
  assert.match(blocked.response.instruction,/ask.*user.*(?:say|repeat).*action.*(?:again|explicit)/i,'blocked model must ask for an explicit action instead of claiming exploration stopped');
  await emit({serverContent:{turnComplete:true}});
  holdResume=true;resolveResume=null;
  // Transcription and tool in one event: tool must await the resume receipt.
  await emit({serverContent:{inputTranscription:{text}},toolCall:{functionCalls:[{id:`recovery-${index}`,name:'phone_explore',args:{}}]}});
  assert.equal(requests.filter(r=>r.url==='/api/voice/resume').length,resumeCount+1,text);
  assert(resolveResume,'resume must be pending');
  assert(!requests.some(r=>r.url==='/api/voice/tool'&&r.body.id===`recovery-${index}`));
  resolveResume();holdResume=false;await tick();
  assert(requests.some(r=>r.url==='/api/voice/tool'&&r.body.id===`recovery-${index}`),'fresh exploration must dispatch after resume');
  finishOld();await tick();
  await emit({toolCall:{functionCalls:[{id:`old-recovery-${index}`,name:'phone_explore',args:{}}]}});
  assert.equal(requests.filter(r=>r.url==='/api/voice/tool'&&r.body.id===`old-recovery-${index}`).length,1,'cancelled call is never replayed');
  assert(!socket.sent.some(m=>m.toolResponse?.functionResponses?.some(r=>r.id===`old-recovery-${index}`)),'cancelled stopped receipt is never returned');
  await emit({toolCall:{functionCalls:[{id:`after-late-${index}`,name:'phone_command',args:{goal:'Open Calculator'}}]}});
  assert(requests.some(r=>r.url==='/api/voice/tool'&&r.body.id===`after-late-${index}`),'late cancelled stop receipt cannot re-latch');
  await el('voice-end').handlers.click();
 }
 console.log('PASS: split stop, cancellation, stale response suppression, late-action latch, explicit new request resume, structured calculation cancellation');
})().catch(e=>{console.error(e);process.exit(1)});
