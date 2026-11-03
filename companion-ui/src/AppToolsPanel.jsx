import React,{useEffect,useState,useRef} from 'react';
import {Play,FileText,Calculator,AlertCircle,Check} from 'lucide-react';
import {api} from './api';
import TaskHistory from './TaskHistory';
import {previewArguments} from './previewArgs';
import MissionPanel from './MissionPanel';
import {checkedSourceFromReceipt,draftArguments} from './sourceDraft';
export default function AppToolsPanel({catalog,receipt,ready,onRefresh,mission,busy}){
 const [app,setApp]=useState('Calculator'),[left,setLeft]=useState('7'),[right,setRight]=useState('8'),[operator,setOperator]=useState('add');
 const [brief,setBrief]=useState('Draft a friendly X post for Joe Speaking, an English-speaking practice app. Invite people to practice speaking today. Do not invent features or pricing.');
 const [query,setQuery]=useState('IELTS speaking'),[text,setText]=useState(''),[current,setCurrent]=useState(null),[result,setResult]=useState(null),[pending,setPending]=useState(false),[error,setError]=useState('');
 const [previewCard,setPreviewCard]=useState(''),[previewTarget,setPreviewTarget]=useState('');
 const [checkedSource,setCheckedSource]=useState(null),[useCheckedSource,setUseCheckedSource]=useState(false);
 useEffect(()=>{setCheckedSource(null);setUseCheckedSource(false);setPreviewCard('');setPreviewTarget('');},[catalog?.server_instance]);
 useEffect(()=>{const source=checkedSourceFromReceipt(receipt,catalog?.server_instance);if(source)setCheckedSource(source);},[receipt,catalog?.server_instance]);
 const lastSeen=useRef(null);
 useEffect(()=>{if(!receipt?.request_id)return;if(receipt.request_id===current){setResult(receipt);}else if(!pending&&receipt.request_id!==lastSeen.current){setCurrent(receipt.request_id);setResult(receipt);setApp(receipt.app||'Calculator');}lastSeen.current=receipt.request_id;},[receipt,current,pending]);
 const tools=catalog?.tools||[],apps=[...new Set(tools.map(t=>t.app))];
 const running=pending||result?.running||mission?.running;
 async function run(tool){
  let args={};setError('');
  if(tool.name==='calculator.calculate'){
   if(left===''||right===''||![left,right].every(n=>Number.isInteger(Number(n))&&Number(n)>=0&&Number(n)<=999999)){setError('Use two whole numbers from 0 to 999999.');return;}
   args={left:Number(left),right:Number(right),operator};
  }else if(tool.name==='grok.preview_reply'){
   try{args=previewArguments(previewCard,previewTarget);}catch(e){setError(e.message);return;}
  }else if(tool.name==='x.prepare_draft'){
   try{args=draftArguments(brief,checkedSource,useCheckedSource);}catch(e){setError(e.message);return;}
  }
  else if(tool.name.endsWith('.search'))args={query};
  else if(tool.name==='keyboard.type_focused')args={text};
  const id=catalog.server_instance+'_'+crypto.randomUUID();setCurrent(id);setPending(true);setResult(null);
  try{setResult(await api('/api/app-tools/call',{request_id:id,server_instance:catalog.server_instance,tool_name:tool.name,args}));await onRefresh();}
  catch(e){setError(e.message);}finally{setPending(false);}
 }
 return <section className="app-tools-view"><header className="page-head"><h1>App tools</h1><p>Open an app, run one skill, or prepare a draft for review. Personal Threads is excluded.</p></header>
 <MissionPanel catalog={catalog} mission={mission} ready={ready} busy={busy} onRefresh={onRefresh}/>{!ready&&<p className="notice">Device tools wait for a stable ESP32 connection, live screen and calibration.</p>}<label className="tool-picker">App<select aria-label="Choose app" value={app} onChange={e=>setApp(e.target.value)}>{apps.map(name=><option key={name}>{name}</option>)}</select></label>
 {tools.filter(t=>t.app===app).map(tool=><article className="app-tool" key={tool.name}><div className="tool-heading"><h3>{tool.name.split('.')[1]==='open'?'Open '+tool.app:tool.name.endsWith('.search')?'Search public content':tool.name==='public.read_results'?'Read search results':tool.name==='calculator.calculate'?'Calculate':tool.name==='x.prepare_draft'?'Prepare an X draft':tool.name==='grok.preview_reply'?'Grok to X preview':'Use app skill'}</h3><span className="effect" data-effect={tool.effect}>{tool.effect==='local_draft'?'Local preparation':tool.effect==='device_read'?'Read verified snapshot':'Real device'}</span></div><p>{tool.description}</p><small>{tool.validation?.status==='visually_verified'?'Device checked · iPad portrait · '+tool.validation.wall_s+'s · one observed run':'Awaiting device validation'}</small>
 {tool.name==='calculator.calculate'&&<div className="calculator-args"><input aria-label="Left number" type="number" min="0" max="999999" value={left} onChange={e=>setLeft(e.target.value)}/><select aria-label="Operator" value={operator} onChange={e=>setOperator(e.target.value)}><option value="add">+</option><option value="subtract">−</option><option value="multiply">×</option></select><input aria-label="Right number" type="number" min="0" max="999999" value={right} onChange={e=>setRight(e.target.value)}/></div>}
 {tool.name==='x.prepare_draft'&&<><label className="tool-brief">What should the draft achieve?<textarea aria-label="Draft brief" maxLength={1000} value={brief} onChange={e=>setBrief(e.target.value)}/></label><label><input type="checkbox" checked={useCheckedSource} disabled={!checkedSource||!tool.parameters?.properties?.source_read_request_id} onChange={e=>setUseCheckedSource(e.target.checked)}/> Use the checked search excerpt{checkedSource?' ('+checkedSource.app+')':''}</label><small>{checkedSource?'Historical public content. Interpretation needs review.':'Run a public search and read its results to attach a source.'}</small></>}
 {tool.name.endsWith('.search')&&<label className="tool-brief">English search query<input aria-label="Search query" maxLength={80} value={query} onChange={e=>setQuery(e.target.value)}/></label>}
 {tool.name==='grok.preview_reply'&&<><label className="tool-brief">Visible card ID<input aria-label="Preview card ID" placeholder="YYYYMMDD-01" maxLength={11} value={previewCard} onChange={e=>setPreviewCard(e.target.value)}/></label><label className="tool-brief">Exact target handle<input aria-label="Preview target handle" placeholder="@target" maxLength={16} value={previewTarget} onChange={e=>setPreviewTarget(e.target.value)}/></label><small>Start on the visible Example Action Workspace card. Copies natively, checks X, previews and discards. Never posts or messages Grok.</small></>}
 {tool.name==='keyboard.type_focused'&&<label className="tool-brief">Exact text<input aria-label="Literal text" maxLength={500} value={text} onChange={e=>setText(e.target.value)}/></label>}
 <div className="tool-action"><button disabled={running||(['device','device_read'].includes(tool.effect)&&!ready)||(tool.name==='x.prepare_draft'&&!brief.trim())||(tool.name.endsWith('.search')&&!query.trim())||(tool.name==='keyboard.type_focused'&&!text)||(tool.name==='grok.preview_reply'&&(!previewCard.trim()||!previewTarget.trim()))} onClick={()=>run(tool)}>{tool.effect==='local_draft'?<FileText size={15}/>:<Play size={15}/>} {tool.effect==='local_draft'?'Prepare draft':tool.name==='grok.preview_reply'?'Preview and discard':'Run tool'}</button><code>{tool.name}</code></div></article>)}
 {error&&<p className="error" role="alert">{error}</p>}
 {(pending||result)&&<article className="tool-result" aria-live="polite"><h3>{pending&&!result?'Working…':(result.status||'Unknown').replaceAll('_',' ')}</h3>{result?.result?.body?<><p className="draft-state"><Check size={14}/> Prepared locally · Not published · Account not checked</p><blockquote>{result.result.body}</blockquote>{result.result.source_evidence&&<div><p>Checked search snapshot: {result.result.source_evidence.app} / {result.result.source_evidence.query}</p>{result.result.source_evidence.results.map((r,i)=><blockquote key={i}>{r.title_quote}{r.byline_quote?' | '+r.byline_quote:''}</blockquote>)}<small>Public content, not verified customer feedback. Business interpretation needs review.</small></div>}</>:result?.result?.summary?<p style={{whiteSpace:'pre-wrap'}}>{result.result.summary}</p>:<p>{(pending?'Preparing your result…':null)||result?.reason||result?.result?.reason||result?.result?.receipt?.reason||(result?.running?'One tool is running. You can stop at any time.':result?.status==='completed'?'The executor finished. Inspect the live device to confirm the result.':'No automatic retry was made.')}</p>}{result?.tool_name==='grok.preview_reply'&&<><p className="draft-state">Preview only · Nothing published{result.result?.cleanup_verified?' · Temporary text discarded':result.result?.paste_attempted?' · Inspect cleanup state':''}</p>{result.result?.phase&&<p>Stage: {result.result.phase.replaceAll('_',' ')}</p>}<ol>{result.result?.phases?.map((p,i)=><li key={i}>{p.phase.replaceAll('_',' ')} · {p.seconds}s</li>)}</ol></>}{result?.elapsed_s!==undefined&&<small>{result.elapsed_s}s · {result.tool_name}</small>}</article>}
 {result?.history_error&&<p className="error" role="alert">The operation may have run, but its receipt could not be saved. Do not repeat input.</p>}
 <TaskHistory catalog={catalog} receipt={receipt}/>
 <p className="tool-footnote">Each capability is shared by chat, voice and MCP. More app skills can be added without changing the device connection.</p></section>;
}
