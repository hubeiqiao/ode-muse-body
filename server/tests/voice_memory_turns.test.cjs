// Companion conversation continuity: voice.js posts finalized transcript turns to the local companion log.
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
  if(url==='/api/voice/tool')return response(h.toolReceipt);
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

test('a completed turn posts the owner and AI text once, text only',async()=>{
 const h=harness();await h.tick();
 await h.startNormal();
 await h.say(['Can you check ','Grubhub?'],['Sure, ','opening Grok Bot.']);
 assert.equal(h.posts().length,0,'nothing posted before the turn completes');
 await h.emit({serverContent:{turnComplete:true}});
 assert.equal(h.posts().length,1);
 const post=h.posts()[0];
 assert.equal(post.method,'POST');
 assert.deepEqual(post.body,{kind:'voice',turns:[{role:'owner',text:'Can you check Grubhub?'},{role:'milo',text:'Sure, opening Grok Bot.'}]});
 assert.ok(!JSON.stringify(post.body).includes('auth_tokens'),'no credential in the log post');
 await h.emit({serverContent:{turnComplete:true}});
 assert.equal(h.posts().length,1,'an empty turn posts nothing');
});

test('ending the session flushes an unfinished turn',async()=>{
 const h=harness();await h.tick();
 await h.startNormal();
 await h.say(['Open Notes please'],[]);
 await h.el('voice-end').handlers.click();await h.tick();
 assert.equal(h.posts().length,1);
 assert.deepEqual(h.posts()[0].body,{kind:'voice',turns:[{role:'owner',text:'Open Notes please'}]});
 assert.equal(h.posts()[0].keepalive,true,'survives page teardown');
});

test('camera turns remain in the same conversation log and socket',async()=>{
 const h=harness();await h.tick();
 const ws=await h.startNormal();h.toolReceipt={status:'camera_opened',terminal:true};
 await h.say(['Look at me with the camera'],[]);
 await h.emit({toolCall:{functionCalls:[{id:'cam-1',name:'open_camera',args:{}}]}});
 await h.say([],['The camera is open.']);await h.emit({serverContent:{turnComplete:true}});
 await h.fire(2500);await h.tick();
 assert.equal(h.requests.filter(r=>r.url==='/api/voice/session').length,1);
 assert.equal(ws.readyState,1);
 assert.deepEqual(h.posts()[0].body,{kind:'voice',turns:[{role:'owner',text:'Look at me with the camera'},{role:'milo',text:'The camera is open.'}]});
 await h.say(['What do you see?'],['I see you waving.']);
 await h.emit({serverContent:{turnComplete:true}});
 assert.equal(h.posts().at(-1).body.kind,'voice');
});

test('a failed log post never breaks the voice session',async()=>{
 const h=harness();await h.tick();
 h.conversationFails=true;
 const ws=await h.startNormal();
 await h.say(['hello'],['hi there']);
 await h.emit({serverContent:{turnComplete:true}});
 assert.equal(ws.readyState,1,'session still open');
 await h.el('voice-end').handlers.click();await h.tick();
 await h.el('voice-start').handlers.click();await h.tick();
 assert.equal(h.requests.filter(r=>r.url==='/api/voice/session').length,2,'next session still mints');
});

(async()=>{
 let failed=0;
 for(const [name,fn] of tests){
  try{await fn();console.log('ok -',name);}
  catch(error){failed++;console.error('not ok -',name,'::',error.message.split('\n')[0]);}
 }
 if(failed){console.error(failed+' voice memory turn check(s) failed');process.exit(1);}
 console.log('voice memory turn checks passed');
})();
