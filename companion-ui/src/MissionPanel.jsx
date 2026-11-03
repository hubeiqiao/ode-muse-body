import React,{useState} from 'react';
import {api} from './api';
import {AiName} from './aiName';

export default function MissionPanel({catalog,mission,ready,busy,onRefresh}){
 const [goal,setGoal]=useState('Find one useful public speaking-practice idea for Joe Speaking and prepare a local post draft for review.'),[pending,setPending]=useState(false),[error,setError]=useState('');
 if(!catalog?.missions?.enabled)return null;
 async function start(){
  setPending(true);setError('');
  try{await api('/api/mission/start',{server_instance:catalog.server_instance,request_id:catalog.server_instance+'_goal_'+crypto.randomUUID(),goal,seconds:300,max_steps:8});await onRefresh();}
  catch(e){setError(e.message);}finally{setPending(false);}
 }
 async function stop(){try{await api('/api/mission/stop',{request_id:mission.request_id});await onRefresh();}catch(e){setError(e.message);}}
 return <section className="mission-panel" aria-label="Work toward a goal"><h2>Work toward a goal</h2><p><AiName/> chooses existing App skills, checks the results, and brings back progress for review. Public research and local drafts only.</p>
 <small>Goal planner: {catalog.missions.model} · Draft writer: {catalog.draft_writer_model} · Device checks: Jev / Haiku</small><label htmlFor="mission-goal">Your goal</label><textarea id="mission-goal" maxLength={1000} value={goal} onChange={e=>setGoal(e.target.value)} disabled={pending||mission?.running}/>
 <div className="mission-actions"><button disabled={!ready||busy||pending||!goal.trim()} onClick={start}>{pending?'Starting…':'Start goal'}</button><button className="stop" disabled={!mission?.running} onClick={stop}>Stop goal</button></div>
 <small>Up to 8 skills / 5 minutes per explicit session. No automatic restart. Voice: “Work toward this goal: …”</small>
 {error&&<p className="error" role="alert">{error}</p>}
 {mission?.request_id&&<article className="tool-result"><h3>{mission.status==='awaiting_feedback'?'Review progress':mission.status.replaceAll('_',' ')}</h3><p>{mission.steps_used}/{mission.max_steps} skills · {mission.elapsed_s}s / {mission.seconds}s{mission.planning_s!==undefined?' · planning '+mission.planning_s+'s':''}</p><p>{mission.goal}</p>
 <ol>{mission.steps?.map(step=><li key={step.request_id}><strong>{step.tool_name}</strong> · {step.status.replaceAll('_',' ')}{step.elapsed_s!==undefined?' · '+step.elapsed_s+'s':''}<p>{step.reason}</p></li>)}</ol>
 {mission.findings?.map(source=><div key={source.request_id}><h4>Checked public snapshot · {source.app}</h4><p>Query: {source.query}</p>{source.results?.map((item,i)=><blockquote key={i}>{item.title_quote}{item.byline_quote?' | '+item.byline_quote:''}</blockquote>)}</div>)}
 {mission.drafts?.map(draft=><div key={draft.request_id}><h4>Local draft · Not published</h4><blockquote>{draft.body}</blockquote></div>)}
 {mission.reason&&<p>{mission.reason}</p>}<small>Public sources are not verified customer feedback. Business interpretations need review. Nothing was written to personal memory or published.</small>
 {mission.receipt_persisted===false&&<p className="error">Goal history could not be saved. Inspect receipts; do not replay input.</p>}</article>}
 </section>;
}
