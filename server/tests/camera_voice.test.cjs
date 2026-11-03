// Continuous screen frames: one stream, late-frame, hidden-tab and backpressure safety.
const assert=require('node:assert/strict'),harness=require('./continuous_voice_harness.cjs');
(async()=>{
 const h=harness();await h.tick();const ws=await h.startNormal();
 h.document.hidden=true;await h.fire(1500);assert.equal(h.videos(ws).length,0);
 h.document.hidden=false;ws.bufferedAmount=300000;await h.fire(1500);assert.equal(h.videos(ws).length,0);
 ws.bufferedAmount=0;await h.fire(1500);assert.equal(h.videos(ws).length,1);
 let release;h.frameHandler=()=>new Promise(r=>{release=r});await h.fire(1500);
 assert(release);await h.el('voice-end').handlers.click();await h.tick();
 release({status:200,arrayBuffer:async()=>new ArrayBuffer(8)});await h.tick();
 assert.equal(h.videos(ws).length,1,'no frame after End');assert.equal(h.pendingMs(1500),0);
 assert.equal(h.sessionRequests().length,1);assert(!h.requests.some(r=>r.url==='/api/voice/camera/frame'));
 console.log('continuous screen stream checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
