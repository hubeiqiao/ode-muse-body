import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';

export default function TaskHistory({catalog,receipt}){
 const [history,setHistory]=useState({items:[],total:0}),[selected,setSelected]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 const epoch=useRef(0),enabled=catalog?.task_history?.enabled===true;
 async function load(offset=0,version=epoch.current,signal){
  setLoading(true);setError('');
  try{
   const value=await api('/api/app-tools/history?limit=20&offset='+offset,undefined,'GET',signal);
   if(version!==epoch.current)return;
   setHistory(old=>({...value,items:offset?[...old.items,...value.items.filter(item=>!old.items.some(v=>v.request_id===item.request_id))]:value.items}));
  }catch(e){if(version===epoch.current&&e.name!=='AbortError')setError(e.message);}
  finally{if(version===epoch.current)setLoading(false);}
 }
 useEffect(()=>{
  const version=++epoch.current,controller=new AbortController();setSelected(null);setHistory({items:[],total:0});
  if(enabled)load(0,version,controller.signal);
  return()=>controller.abort();
 },[catalog?.server_instance,enabled,receipt?.request_id,receipt?.status]);
 async function view(requestId){
  const version=epoch.current;setError('');
  try{const value=await api('/api/app-tools/status?request_id='+encodeURIComponent(requestId),undefined,'GET');if(version===epoch.current)setSelected(value);}
  catch(e){if(version===epoch.current)setError(e.message);}
 }
 return <section className="task-history" aria-label="Saved task history"><div className="section-heading"><div><h2>Task history</h2><p>Local receipts survive a service restart. Viewing them never runs a tool.</p></div>{enabled&&<button onClick={()=>load()} disabled={loading}>Refresh history</button>}</div>
 {!enabled?<p>Saved task history is not enabled for this console.</p>:<>
 {error&&<p className="error" role="alert">{error}</p>}
 {!history.items.length&&<p>{loading?'Loading receipts…':'No saved tasks yet.'}</p>}
 <ul className="task-history-list">{history.items.map(item=><li key={item.request_id}><div><strong>{item.tool_name}{item.request_id.includes('_qa_')?' · QA':''}</strong><small>{new Date(item.created_at*1000).toLocaleString('en-CA')}</small></div><span className="status" data-status={item.status}>{item.status.replaceAll('_',' ')}</span><button onClick={()=>view(item.request_id)}>View receipt</button></li>)}</ul>
 {history.items.length<history.total&&<button disabled={loading} onClick={()=>load(history.items.length)}>Show more</button>}
 {selected&&<article className="tool-result"><h3>Saved receipt · {selected.status.replaceAll('_',' ')}</h3><p>Read-only task record. Not live device proof, personal memory or permission to replay.</p>{selected.result?.body&&<><p>Local draft · Not published</p><blockquote>{selected.result.body}</blockquote></>}{selected.result?.source_evidence&&<p>Historical source: {selected.result.source_evidence.app} / {selected.result.source_evidence.query}. Interpretation needs review.</p>}{selected.result?.drafts?.map(draft=><div key={draft.request_id}><p>Saved local draft · Not published</p><blockquote>{draft.body}</blockquote></div>)}{selected.result?.findings?.map(source=><div key={source.request_id}><p>Historical public snapshot · {source.app} / {source.query}</p>{source.results?.map((q,i)=><blockquote key={i}>{q.title_quote}{q.byline_quote?' | '+q.byline_quote:''}</blockquote>)}</div>)}{selected.result?.summary&&<p style={{whiteSpace:'pre-wrap'}}>{selected.result.summary}</p>}{(selected.result?.reason||selected.result?.receipt?.reason)&&<p>{selected.result.reason||selected.result.receipt.reason}</p>}{(selected.result?.never_retry_typing||selected.result?.receipt?.never_retry_typing)&&<p className="error">Input may already be present. Do not resend it.</p>}<small>{selected.request_id}</small></article>}
 </>}
 </section>;
}
