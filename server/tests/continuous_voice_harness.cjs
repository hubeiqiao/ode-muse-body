// Offline browser harness: fake audio, fetch, WebSocket and timers.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
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
 const h={el,sockets:[],requests:[],nodes:[],toolReceipt:{status:'switch_to_camera',terminal:true,instruction:'Tell the owner you are switching to the camera conversation now.'},cameraSession:null};
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
  createBufferSource(){const node={connect(){},start(){},stop(){},onended:null};h.nodes.push(node);return node;}
 }
 const stream={getTracks:()=>[{stop(){}}],getAudioTracks:()=>[{enabled:true}]};
 const response=(data,status=200)=>({ok:status<300,status,json:async()=>data,arrayBuffer:async()=>new ArrayBuffer(0)});
 const JPEG=Uint8Array.from([0xff,0xd8,0xff,0xe0,1,2,3,0xff,0xd9]);
 async function fetch(url,opts={}){
  const body=opts.body?JSON.parse(opts.body):{};h.requests.push({url,body});
  if(url==='/api/voice/status')return response({configured:true,camera_controls:true});
  if(url==='/api/voice/session'){
   if(body.camera)return h.cameraSession?h.cameraSession():response({session:'k'.repeat(32),token:'auth_tokens/cam',setup:{},camera:{generation:'gen-1',frame_interval_ms:1000,max_s:540}});
   return response({session:'s'.repeat(32),token:'auth_tokens/test',setup:{}});
  }
  if(url.startsWith('/api/voice/task_status?'))return h.taskHandler?h.taskHandler(url):response(h.taskReceipt);
  if(url==='/api/voice/tool')return h.toolHandler?h.toolHandler(body):response(h.toolReceipt);
  if(url==='/api/voice/screen/frame')return h.frameHandler?h.frameHandler(body):{ok:true,status:200,json:async()=>({}),arrayBuffer:async()=>JPEG.buffer.slice(0)};
  if(url==='/api/voice/interrupt')return response({status:'stopped',paused:true});
  return response({ok:true});
 }
 let seq=0;const timers=new Map();h.timers=timers;
 const setT=(fn,ms)=>{const id=++seq;timers.set(id,{fn,ms});return id};
 const clearT=id=>{timers.delete(id)};
 h.tick=async()=>{for(let i=0;i<10;i++)await new Promise(setImmediate)};
 h.fire=async ms=>{for(const [id,t] of [...timers.entries()].filter(([,t])=>t.ms===ms)){timers.delete(id);t.fn();}await h.tick();};
 h.pendingMs=ms=>[...timers.values()].filter(t=>t.ms===ms).length;
 const context={document:{documentElement:{lang:'en'},body:{dataset:{}},getElementById:el,createElement:()=>new Element(),createTextNode:text=>({textContent:text}),addEventListener(){}},
  window:{PhoneVoiceIntents:require(path.join(root,'voice-intents.js')),addEventListener(){}},
  navigator:{mediaDevices:{getUserMedia:async()=>stream}},AudioContext:Audio,AudioWorkletNode:class{constructor(){this.port={}}connect(){}disconnect(){}},
  WebSocket:WS,fetch,Date,JSON,Promise,TextDecoder,ArrayBuffer,DataView,Uint8Array,Float32Array,setTimeout:setT,clearTimeout:clearT,console,btoa,atob};
 h.body=context.document.body;h.document=context.document;
 vm.runInNewContext(source,context);
 h.emit=async data=>{h.socket.onmessage({data:JSON.stringify(data)});await h.tick()};
 h.videos=s=>s.sent.filter(m=>m.realtimeInput?.video);
 h.sessionRequests=()=>h.requests.filter(r=>r.url==='/api/voice/session');
 h.startNormal=async()=>{await el('voice-start').handlers.click();h.socket.onopen();await h.emit({setupComplete:{}});return h.socket;};
 h.askCamera=async()=>{
  await h.emit({serverContent:{inputTranscription:{text:'Can you look at me with the camera?'}}});
  await h.emit({toolCall:{functionCalls:[{id:'cam-1',name:'open_camera',args:{}}]}});
 };
 return h;
}

module.exports=harness;
