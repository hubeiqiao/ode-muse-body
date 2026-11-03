// Realtime voice (owner 2026-10-29): interjection forwarding, started/progress/stop-summary display.
// Fake WebSocket/fetch/timers/audio; no network, no model, no device.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.join(__dirname,'../hidagent/static');
const source=fs.readFileSync(path.join(root,'voice.js'),'utf8');

function harness(){
 class Element {
  constructor(){this.children=[];this.dataset={};this.handlers={};this.disabled=false;this.hidden=false;this.open=false;this.textContent='';}
  addEventListener(k,f){this.handlers[k]=f;}
  append(...nodes){this.children.push(...nodes);}
  remove(){}
  get lastElementChild(){return this.children.at(-1);}
  get firstElementChild(){return this.children[0];}
  get lastChild(){return this.children.at(-1);}
 }
 const els=new Map();const el=id=>{if(!els.has(id))els.set(id,new Element());return els.get(id)};
 const h={el,sockets:[],requests:[],toolReceipt:{status:'switch_to_camera',terminal:true}};
 class WS {
  static OPEN=1;
  constructor(){h.socket=this;h.sockets.push(this);this.readyState=1;this.sent=[];this.bufferedAmount=0;}
  send(s){if(this.readyState!==1)throw new Error('send after close');this.sent.push(JSON.parse(s))} close(){this.readyState=3;}
 }
 class Audio {
  constructor(){this.state='running';this.sampleRate=16000;this.currentTime=0;this.audioWorklet={addModule:async()=>{}};this.destination={};}
  async resume(){} async close(){this.state='closed'}
  createMediaStreamSource(){return {connect(){},disconnect(){}}}
  createGain(){return {gain:{value:0},connect(){}}}
  createBuffer(_c,n,rate){return {duration:n/rate,getChannelData:()=>new Float32Array(n)}}
  createBufferSource(){return {connect(){},start(){},stop(){},onended:null};}
 }
 const stream={getTracks:()=>[{stop(){}}],getAudioTracks:()=>[{enabled:true}]};
 const response=(data,status=200)=>({ok:status<300,status,json:async()=>data,arrayBuffer:async()=>new ArrayBuffer(0)});
 async function fetch(url,opts={}){
  const body=opts.body?JSON.parse(opts.body):{};h.requests.push({url,body,method:opts.method,keepalive:opts.keepalive});
  if(url==='/api/voice/status')return response({configured:true,camera_conversation:true});
  if(url==='/api/voice/session'){
   if(body.camera)return response({session:'k'.repeat(32),token:'auth_tokens/cam',setup:{},camera:{generation:'gen-1',frame_interval_ms:1000,max_s:540}});
   return response({session:'s'.repeat(32),token:'auth_tokens/test',setup:{}});
  }
  if(url==='/api/companion/conversation'){if(h.conversationFails)throw new Error('offline');return response({stored:body.turns.length,refused:0});}
  if(url==='/api/voice/tool')return response(typeof h.toolReceipt==='function'?h.toolReceipt(body):h.toolReceipt);
  if(url==='/api/voice/camera/frame')return {ok:true,status:204,json:async()=>({})};
  return response({ok:true});
 }
 let seq=0;const timers=new Map();
 const setT=(fn,ms)=>{const id=++seq;timers.set(id,{fn,ms});return id};
 const clearT=id=>{timers.delete(id)};
 h.tick=async()=>{for(let i=0;i<10;i++)await new Promise(setImmediate)};
 h.fire=async ms=>{for(const [id,t] of [...timers.entries()].filter(([,t])=>t.ms===ms)){timers.delete(id);t.fn();}await h.tick();};
 const context={document:{documentElement:{lang:'en'},body:{dataset:{}},getElementById:el,createElement:()=>new Element(),createTextNode:text=>({textContent:text}),addEventListener(){}},
  window:{PhoneVoiceIntents:require(path.join(root,'voice-intents.js')),addEventListener(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>stream}},AudioContext:Audio,AudioWorkletNode:class{constructor(){this.port={}}connect(){}disconnect(){}},
  WebSocket:WS,fetch,Date,JSON,Promise,TextDecoder,ArrayBuffer,DataView,Uint8Array,Float32Array,setTimeout:setT,clearTimeout:clearT,console,btoa,atob};
 vm.runInNewContext(source,context);
 h.emit=async data=>{h.socket.onmessage({data:JSON.stringify(data)});await h.tick()};
 h.startNormal=async()=>{await el('voice-start').handlers.click();h.socket.onopen();await h.emit({setupComplete:{}});return h.socket;};
 h.startCamera=async()=>{await el('voice-camera-start').handlers.click();h.socket.onopen();await h.emit({setupComplete:{}});return h.socket;};
 h.posts=()=>h.requests.filter(r=>r.url==='/api/companion/conversation');
 h.say=async(owner,milo)=>{
  for(const part of owner)await h.emit({serverContent:{inputTranscription:{text:part}}});
  for(const part of milo)await h.emit({serverContent:{outputTranscription:{text:part}}});
 };
 return h;
}

const tests=[];const test=(name,fn)=>tests.push([name,fn]);
const toolPosts=h=>h.requests.filter(r=>r.url==='/api/voice/tool');
const responses=ws=>ws.sent.flatMap(m=>m.toolResponse?.functionResponses||[]);
const transcript=h=>h.el('voice-transcript').children.map(r=>r.lastChild.textContent);

test('add_to_current_task forwards the owner utterance and is answered',async()=>{
 const h=harness();await h.tick();
 h.toolReceipt=body=>body.name==='add_to_current_task'?{status:'note_added',terminal:true,note_added:true,applies:'before_next_step'}:{status:'started',running:true,terminal:false};
 const ws=await h.startNormal();
 await h.emit({toolCall:{functionCalls:[{id:'t1',name:'phone_command',args:{goal:'Open Notes'}}]}});
 await h.emit({serverContent:{outputTranscription:{text:'On it.'}}});
 await h.emit({serverContent:{inputTranscription:{text:'also add milk'}}});
 await h.emit({toolCall:{functionCalls:[{id:'n1',name:'add_to_current_task',args:{text:'also add milk'}}]}});
 const post=toolPosts(h).find(r=>r.body.id==='n1');
 assert.ok(post,'note posted to the server');
 assert.equal(post.body.utterance,'also add milk','owner words go with the note');
 assert.equal(toolPosts(h).find(r=>r.body.id==='t1').body.utterance,'','phone_command utterance unchanged');
 assert.equal(responses(ws).find(r=>r.id==='n1').response.status,'note_added');
 assert.equal(h.requests.filter(r=>r.url==='/api/voice/interrupt').length,0,'a correction is not a stop');
 assert.ok(transcript(h).some(t=>/passed to the running task/i.test(t)),transcript(h).join('|'));
});

test('a started receipt is shown as started, not completed',async()=>{
 const h=harness();await h.tick();
 h.toolReceipt={status:'started',running:true,terminal:false,goal:'Open Notes',task_id:'1'};
 await h.startNormal();
 await h.emit({toolCall:{functionCalls:[{id:'s1',name:'phone_command',args:{goal:'Open Notes'}}]}});
 assert.ok(transcript(h).includes('Started. Working on it…'),transcript(h).join('|'));
});

test('a progress line from phone_status is shown to the owner',async()=>{
 const h=harness();await h.tick();
 h.toolReceipt={status:'in_progress',running:true,terminal:false,goal:'Open Notes',task_id:'1',progress:{line:'Tapping the Notes result.',steps_done:2}};
 await h.startNormal();
 await h.emit({toolCall:{functionCalls:[{id:'p1',name:'phone_status',args:{}}]}});
 assert.ok(transcript(h).some(t=>t.includes('Tapping the Notes result.')),transcript(h).join('|'));
});

test('the stop summary line is shown after a stop receipt',async()=>{
 const h=harness();await h.tick();
 h.toolReceipt={status:'stopped',terminal:true,paused:true,goal:'Open Notes',task_id:'1',stop_summary:{was_running:true,line:'Stopped after 4 steps (last: type, tap, type) in 12.4 s. Text was typed. Not finished: Open Notes.'}};
 await h.startNormal();
 await h.emit({toolCall:{functionCalls:[{id:'x1',name:'stop_phone',args:{}}]}});
 assert.ok(transcript(h).some(t=>t.includes('Stopped after 4 steps')),transcript(h).join('|'));
});

(async()=>{
 let failed=0;
 for(const [name,fn] of tests){
  try{await fn();console.log('ok -',name);}
  catch(error){failed++;console.error('not ok -',name,'::',error.message.split('\n')[0]);}
 }
 if(failed){console.error(failed+' voice realtime check(s) failed');process.exit(1);}
 console.log('voice realtime checks passed');
})();
