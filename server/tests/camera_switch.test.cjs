// Camera controls stay in one continuous Live conversation.
const assert=require('node:assert/strict'),harness=require('./continuous_voice_harness.cjs');
(async()=>{
 const h=harness();await h.tick();h.toolReceipt={status:'camera_opened',terminal:true};
 const ws=await h.startNormal();
 await h.fire(1500);
 assert.equal(h.videos(ws).length,1);
 await h.askCamera();await h.fire(2500);await h.fire(1500);
 assert.equal(ws.readyState,1);assert.equal(h.sockets.length,1);assert.equal(h.sessionRequests().length,1);
 assert.equal(h.videos(ws).length,2,'screen stream continues after opening Camera');
 assert(!h.requests.some(r=>r.url==='/api/voice/end'||r.url==='/api/voice/camera/frame'));
 const req=h.requests.find(r=>r.url==='/api/voice/tool');
 assert.equal(req.body.name,'open_camera');assert.match(req.body.utterance,/look at me/);assert.equal(req.body.owner_turn,1);
 await h.el('voice-camera-start').handlers.click();await h.tick();
 assert.equal(h.sockets.length,1);assert.equal(h.sessionRequests().length,1);
 assert(ws.sent.some(m=>m.clientContent?.turns?.[0]?.parts?.[0]?.text==='Open the camera.'));
 assert(!JSON.stringify(h.requests).includes('switch_to_camera'));
 console.log('continuous camera button and tool checks passed');
})().catch(e=>{console.error(e);process.exit(1)});
