import {draftReviewText} from './sourceDraft';
import {explorationResultText} from './explorationResult';
export async function api(path,body,method='POST',signal){
 const response=await fetch(path,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)}),signal});
 const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Request could not be completed');return data;
}
export const get=path=>api(path,undefined,'GET');
export async function* runCompanion({messages,abortSignal}){
 const last=messages.at(-1),text=last.content.filter(p=>p.type==='text').map(p=>p.text).join('\n');
 let roundId,missionId;
 const cancel=()=>{if(missionId)void api('/api/mission/stop',{request_id:missionId}).catch(()=>{});if(roundId)void api('/api/exploration/stop',{round_id:roundId}).catch(()=>{});};
 abortSignal.addEventListener('abort',cancel,{once:true});
 try{
  const reply=await api('/api/companion/chat',{text,server_instance:document.documentElement.dataset.serverInstance,request_id:crypto.randomUUID(),history:messages.slice(-12,-1).map(m=>({role:m.role,text:m.content.filter(p=>p.type==='text').map(p=>p.text).join('\n')}))});
  roundId=reply.round_id;missionId=reply.mission_request_id;if(abortSignal.aborted){cancel();return;}
  yield {content:[{type:'text',text:reply.text}]};window.dispatchEvent(new Event('companion-refresh'));
  if(reply.mission_request_id){
   const instance=document.documentElement.dataset.serverInstance;
   for(let n=0;n<305;n++){
    if(abortSignal.aborted)return;
    const mission=await get('/api/mission/status');
    if(mission.server_instance!==instance||mission.request_id!==missionId)throw new Error('The goal session changed. No old task was replayed.');
    if(mission.running){await new Promise(r=>setTimeout(r,1000));continue;}
    const sources=(mission.findings||[]).map(s=>s.app+' / '+s.query+'\n'+s.results.map(r=>r.title_quote+(r.byline_quote?' | '+r.byline_quote:'')).join('\n')).join('\n\n');
    const drafts=(mission.drafts||[]).map(d=>d.body).join('\n\n');
    yield {content:[{type:'text',text:(sources?'Checked public snapshots:\n'+sources+'\n\n':'')+(drafts?'Local drafts for review, not published:\n'+drafts+'\n\n':'')+(mission.reason||'This goal session ended. Review actual progress.')}]};window.dispatchEvent(new Event('companion-refresh'));return;
   }
   return;
  }
  if(reply.tool_request_id){
   const id=reply.tool_request_id,instance=document.documentElement.dataset.serverInstance;
   for(let n=0;n<95;n++){
    const tool=await get('/api/app-tools/status?request_id='+encodeURIComponent(id));
    if(tool.server_instance!==instance)throw new Error('Server restarted. Inspect the device; no request was replayed.');
    if(abortSignal.aborted)return;
    if(tool.running){await new Promise(r=>setTimeout(r,1000));continue;}
    yield {content:[{type:'text',text:tool.result?.body?draftReviewText(tool.result):tool.result?.summary||tool.reason||tool.result?.reason||tool.result?.receipt?.reason||('App tool: '+tool.status+'. Check the live device for visible confirmation.')} ]};
    window.dispatchEvent(new Event('companion-refresh'));return;
   }
   return;
  }
  if(!roundId)return;
  const deadline=Date.now()+95000;
  while(Date.now()<deadline){
   await new Promise(r=>setTimeout(r,1000));if(abortSignal.aborted)return;
   const state=await get('/api/exploration/status');
   if(state.round_id!==roundId)throw new Error('The active round changed. No replay was attempted.');
   if(state.busy){yield {content:[{type:'text',text:reply.text+(state.proposal?'\n\nInvestigating: '+state.proposal.topic:'')}]};continue;}
   yield {content:[{type:'text',text:explorationResultText(state)}]};
   window.dispatchEvent(new Event('companion-refresh'));return;
  }
  yield {content:[{type:'text',text:'The round has not returned a final receipt. Check the live status before starting anything else.'}]};
 }finally{abortSignal.removeEventListener('abort',cancel);}
}
