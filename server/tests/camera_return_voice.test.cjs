// Camera actions and later device actions all use the original socket.
const assert=require('node:assert/strict'),harness=require('./continuous_voice_harness.cjs');
(async()=>{
 const h=harness();await h.tick();const ws=await h.startNormal();
 for(const [i,name,status] of [[1,'flip_camera','camera_flipped'],[2,'take_photo','photo_uncertain'],[3,'phone_command','started']]){
  h.toolReceipt={status,terminal:status!=='started',running:status==='started'};
  await h.emit({serverContent:{inputTranscription:{text:name==='phone_command'?'Open Notes':name.replace('_',' ')}}});
  const call={id:String(i),name,args:name==='phone_command'?{goal:'Open Notes'}:{}};
  await h.emit({toolCall:{functionCalls:[call]}});await h.emit({toolCall:{functionCalls:[call]}});
  assert.equal(h.requests.filter(r=>r.url==='/api/voice/tool'&&r.body.id===String(i)).length,1);
  await h.emit({serverContent:{turnComplete:true}});await h.fire(1500);
 }
 assert.equal(h.sockets.length,1);assert.equal(ws.readyState,1);assert.equal(h.sessionRequests().length,1);
 assert.equal(h.videos(ws).length,7);  // 3 periodic + 1 fresh frame before each of 4 tool results
 const rows=h.el('voice-transcript').children.map(r=>r.lastChild.textContent).join('|');
 assert.match(rows,/photo not confirmed/i);assert(!/switching|camera conversation/i.test(rows));
 // Spoken Stop keeps this conversation open and latches further actions.
 await h.emit({serverContent:{inputTranscription:{text:'stop'}}});
 const before=h.requests.filter(r=>r.url==='/api/voice/tool').length;
 await h.emit({toolCall:{functionCalls:[{id:'blocked-photo',name:'take_photo',args:{}}]}});
 assert.equal(h.requests.filter(r=>r.url==='/api/voice/tool').length,before);
 assert.equal(ws.readyState,1);assert(h.requests.some(r=>r.url==='/api/voice/interrupt'));
 console.log('continuous camera receipt and Stop checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
