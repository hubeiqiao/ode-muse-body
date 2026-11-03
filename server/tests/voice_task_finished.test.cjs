// Offline Live transport regression: the browser owns completion, never the model.
const assert = require('node:assert/strict');
const harness = require('./continuous_voice_harness.cjs');
const finished = ws => ws.sent.filter(m => m.clientContent?.turns?.[0]?.parts?.[0]?.text?.startsWith('[TASK FINISHED]'));
const call = (h, id, name = 'phone_command') => h.emit({toolCall:{functionCalls:[{id,name,args:{goal:'Find something fun'}}]}});
(async () => {
 for (const name of ['phone_command','use_app_tool','phone_explore','start_app_goal','ipad_demo','open_camera','flip_camera','take_photo']) {
  const h = harness(); await h.tick();
  h.toolReceipt = {status:'in_progress',running:true,terminal:false,task_id:'task-1'};
  h.taskReceipt = {status:'in_progress',running:true,terminal:false};
  const ws = await h.startNormal();
  await call(h,'one',name); await call(h,'one',name);
  await h.fire(1500);
  assert.equal(finished(ws).length,0);
  assert.equal(h.requests.filter(r=>r.url.startsWith('/api/voice/task_status?')).length,1, name+' has one poll loop');
  h.taskReceipt = {status:'completed',running:false,terminal:true,result:{answer:'Found a fun public article.'},reason:'checked',evidence:[{claim:'A checked fact'}]};
  await h.fire(1500); await h.fire(1500);
  assert.equal(finished(ws).length,1,name+' announces exactly once');
  const message=finished(ws)[0];
  assert.equal(message.clientContent.turnComplete,true);
  assert.match(message.clientContent.turns[0].parts[0].text,/Found a fun public article/);
  assert.match(message.clientContent.turns[0].parts[0].text,/Do not call tools/);
  await call(h,'one',name); await h.fire(1500);
  assert.equal(finished(ws).length,1,'duplicate receipt cannot rearm completion');
 }
 for (const status of ['failed','stopped','uncertain','verified_discovery','expired','awaiting_confirmation']) {
  const h=harness(); await h.tick(); h.toolReceipt={status:'started',running:true,terminal:false};
  h.taskReceipt={status,running:false,terminal:true,evidence:[{claim:'Checked discovery'}],readback:'Exact draft',draft_id:'draft-1'};
  const ws=await h.startNormal(); await call(h,'terminal'); await h.fire(1500); await h.fire(1500);
  assert.equal(finished(ws).length,1,status+' is announced');
  assert.match(finished(ws)[0].clientContent.turns[0].parts[0].text,new RegExp(status));
  if(status==='awaiting_confirmation') assert.match(finished(ws)[0].clientContent.turns[0].parts[0].text,/Exact draft/);
 }
 // A transient status error neither announces completion nor redispatches input.
 {
  const h=harness(); await h.tick(); h.toolReceipt={status:'started',running:true,terminal:false};
  h.taskHandler=()=>{throw Error('offline')}; const ws=await h.startNormal(); await call(h,'retry');
  await h.fire(1500); assert.equal(finished(ws).length,0);
  h.taskHandler=null; h.taskReceipt={status:'failed',terminal:true,reason:'No results'};
  await h.fire(1500); assert.equal(finished(ws).length,1);
  assert.equal(h.requests.filter(r=>r.url==='/api/voice/tool').length,1);
 }
 // An in-flight status from an ended session cannot speak into the next session.
 {
  const h=harness(); await h.tick(); h.toolReceipt={status:'started',running:true,terminal:false};
  let resolve; h.taskHandler=()=>new Promise(r=>{resolve=r});
  const ws=await h.startNormal(); await call(h,'old'); await h.fire(1500);
  await h.el('voice-end').handlers.click(); await h.startNormal();
  resolve({ok:true,json:async()=>({status:'completed',terminal:true,answer:'stale'})}); await h.tick();
  assert.equal(finished(ws).length,0); assert.equal(finished(h.socket).length,0);
 }
 // A new task can start before the previous completion poll returns.
 {
  const h=harness(); await h.tick(); const ws=await h.startNormal();
  h.toolReceipt={status:'started',running:true,terminal:false,task_watch_id:'old'}; await call(h,'old');
  h.toolReceipt={status:'started',running:true,terminal:false,task_watch_id:'new'}; await call(h,'new');
  h.taskHandler=url=>({ok:true,json:async()=>({status:'completed',terminal:true,answer:url.includes('task=old')?'old result':'new result'})});
  await h.fire(1500); await h.fire(1500);
  assert.equal(finished(ws).length,2,'each task keeps its own receipt');
  assert.ok(finished(ws).some(m=>m.clientContent.turns[0].parts[0].text.includes('old result')));
  assert.ok(finished(ws).some(m=>m.clientContent.turns[0].parts[0].text.includes('new result')));
 }
 console.log('voice task completion checks passed');
})().catch(e=>{console.error(e);process.exitCode=1});
