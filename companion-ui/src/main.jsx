import React,{useState,useEffect,useCallback} from 'react';
import {createRoot} from 'react-dom/client';
import {AssistantRuntimeProvider,useLocalRuntime,ThreadPrimitive,MessagePrimitive,ComposerPrimitive} from '@assistant-ui/react';
import {MessageCircle,Newspaper,SquareCheck,Shapes,Tablet,Settings,ExternalLink,ArrowUp,Plus,Trash2,Pencil,Square,Check,ChevronRight} from 'lucide-react';
import {api,get,runCompanion} from './api';
import './style.css';
import AppToolsPanel from './AppToolsPanel';
import {VoiceOrbButtons,VoiceSideButtons,VoiceStatus,VoiceTranscript,useVoiceState} from './VoicePanel';
import {loadVoiceScripts} from './voiceLoader.mjs';
import {mirrorMessage,mirrorAge,modelRoute} from './device-status.mjs';
import {liveLine} from './live.mjs';
import {useAiName,loadAiName} from './aiName';
import NameSetting from './NameSetting';
import ClearChat,{chatClearedAt} from './ClearChat';

// The one avatar slot (original Ode pebble art, transparent PNG). To swap it, replace
// companion-ui/public/ode-avatar-128.png and -256.png (served from /static/companion/). If they are
// missing, the initial shows instead.
const AVATAR={sm:import.meta.env.BASE_URL+'ode-avatar-128.png',lg:import.meta.env.BASE_URL+'ode-avatar-256.png'};
function Avatar({name,size='sm'}){
 const [failed,setFailed]=useState(false);
 return <span className="avatar" data-size={size} data-img={!failed} aria-hidden="true">{failed?<span className="avatar-initial">{(name||'O').trim()[0]?.toUpperCase()}</span>:<img src={AVATAR[size]} alt="" draggable={false} onError={()=>setFailed(true)}/>}</span>;
}

function Message({user=false}){return <MessagePrimitive.Root data-role={user?"user":"assistant"} className="message"><div className="message-body"><MessagePrimitive.Parts/><MessagePrimitive.Error><span className="error">The request could not be completed. Check the live status before retrying.</span></MessagePrimitive.Error></div></MessagePrimitive.Root>;}
function UserMessage(){return <Message user/>;}
function SourceCard({item}){return <article className="source-card"><div className="source-label"><Tablet size={16}/> From the real iPad <Check size={16}/></div><h3>{item.topic||'A checked discovery'}</h3><p>{item.claim}</p><blockquote>“{item.quote}”</blockquote><a href={item.url} target="_blank" rel="noreferrer">{new URL(item.url).hostname}<ExternalLink size={14}/></a><small>Observed {new Date((item.observed_at_wall||item.created_at)*1000).toLocaleString()} · {item.verifier}</small></article>;}

// Settings sheet: a bottom sheet on phones, a centred card on wider screens. Content stays mounted.
function Sheet({open,title,onClose,children}){
 return <section className="sheet" data-open={open} aria-hidden={!open} inert={!open} role="dialog" aria-label={title}>
  <header className="sheet-head"><div/><h2>{title}</h2><div className="sheet-side"><button className="nav-done" onClick={onClose}>Done</button></div></header>
  <div className="sheet-body">{children}</div>
 </section>;
}
function Row({icon:Icon,label,detail,onClick,href}){
 const inner=<>{Icon&&<span className="row-icon"><Icon size={18}/></span>}<span className="row-label">{label}</span>{detail!==undefined&&<span className="row-detail">{detail}</span>}{href?<ExternalLink size={16} className="row-chevron"/>:<ChevronRight size={18} className="row-chevron"/>}</>;
 return href?<a className="row" href={href} target="_blank" rel="noreferrer">{inner}</a>:<button className="row" onClick={onClick}>{inner}</button>;
}
function ViewHead({title,note,action}){return <header className="view-head"><div><h1>{title}</h1>{note&&<p>{note}</p>}</div>{action}</header>;}

const ORB_LABEL={idle:'',connecting:'Connecting',listening:'Listening',speaking:'Speaking',working:'Working'};
const VIEWS=[{id:'chat',label:'Chat',icon:MessageCircle},{id:'feed',label:'Feed',icon:Newspaper},{id:'goals',label:'Goals',icon:SquareCheck},{id:'library',label:'Library',icon:Shapes},{id:'ipad',label:'iPad',icon:Tablet}];
const SUBTITLE="Muse's body on this iPad";

function App(){
 const runtime=useLocalRuntime({run:runCompanion});
 const aiName=useAiName();
 const {state:voice,notice,progress:voiceProgress}=useVoiceState();
 const [connectionError,setConnectionError]=useState('');
 const [clearedAt,setClearedAt]=useState(chatClearedAt);
 const [mission,setMission]=useState(null);
 const [catalog,setCatalog]=useState(null),[toolReceipt,setToolReceipt]=useState(null);
 const [view,setView]=useState('chat'),[settings,setSettings]=useState(false);
 const [memory,setMemory]=useState({preferences:[],discoveries:[]}),[status,setStatus]=useState(null),[error,setError]=useState(''),[frame,setFrame]=useState(''),[editing,setEditing]=useState(null),[draft,setDraft]=useState(''),[saving,setSaving]=useState(false);
 const refresh=useCallback(async()=>{try{const [s,m,c,t]=await Promise.all([get('/api/status'),get('/api/companion/memory'),get('/api/app-tools/catalog'),get('/api/app-tools/status')]);setStatus(s);setMemory(m);setCatalog(c);setToolReceipt(t);void loadAiName().catch(()=>{});setMission(c.missions?.enabled?await get('/api/mission/status'):null);document.documentElement.dataset.serverInstance=c.server_instance;setConnectionError('');}catch(e){setStatus(null);setFrame('');setConnectionError(e.message);}},[]);
 useEffect(()=>{refresh();const timer=setInterval(()=>{if(!document.hidden)refresh();},1500);window.addEventListener('companion-refresh',refresh);return()=>{clearInterval(timer);window.removeEventListener('companion-refresh',refresh);};},[refresh]);
 useEffect(()=>{if(!status?.screen){setFrame('');return;}setFrame('/api/frame.jpg?t='+Date.now());const timer=setInterval(()=>{if(!document.hidden)setFrame('/api/frame.jpg?t='+Date.now());},2000);return()=>clearInterval(timer);},[status?.screen]);
 useEffect(()=>{loadVoiceScripts().catch(()=>setError('Voice controls could not load. Refresh this page.'));},[]);
 async function stop(){try{await api('/api/agent/stop',{});runtime.thread.cancelRun();await refresh();}catch(e){setError(e.message);}}
 useEffect(()=>{const handler=e=>{if(e.key!=='Escape')return;if(settings)setSettings(false);else void stop();};document.addEventListener('keydown',handler);return()=>document.removeEventListener('keydown',handler);});
 function add(){setEditing({id:null});setDraft('');setView('library');}
 async function save(){if(!draft.trim()||saving)return;setSaving(true);try{await (editing.id?api('/api/companion/memory/'+editing.id,{expected:editing.text,text:draft},'PUT'):api('/api/companion/memory',{text:draft}));setEditing(null);setDraft('');await refresh();}catch(e){setError(e.message);}finally{setSaving(false);}}
 async function remove(item){try{await api('/api/companion/memory/'+item.id,{expected:item.text||item.claim},'PUT');await refresh();}catch(e){setError(e.message);}}
 async function restore(item){try{await api('/api/companion/memory/'+item.id,{expected:item.text||item.claim,restore:true},'PUT');await refresh();}catch(e){setError(e.message);}}
 const ex=status?.exploration||{},ready=status?.device&&status?.screen&&status?.calibrated&&(status?.controller?.stable??true),busy=ex.busy||status?.agent?.running||toolReceipt?.running||mission?.running||status?.control_activity?.app_tool_running;
 // The inline discovery card belongs to the chat: after Clear chat only newer discoveries show there (Feed keeps all).
 const last=memory.discoveries.at(-1),latest=last&&(last.observed_at_wall||last.created_at||0)>clearedAt?last:null;
 const esp=!status?.device?'offline':status?.controller?.stable?'ready':'checking';
 const deviceTone=!status?'off':status.screen&&status.device?(ready?'on':'partial'):status.device||status.screen?'partial':'off';
 const deviceState=busy?(toolReceipt?.running?'Using '+toolReceipt.tool_name:ex.proposal?'Exploring '+ex.proposal.topic:'Working on your request'):!status?.screen?mirrorMessage(status):ex.state==='stopped'?'Stopped. Waiting for you.':ready?'Ready when you are.':!status?.device?'ESP32 disconnected. Voice and memory still work.':!status?.calibrated?'Current calibration is not ready.':'Checking the ESP32 connection before the next action.';
 const progress=busy&&(toolReceipt?.running?`${toolReceipt.elapsed_s}s · ${toolReceipt.tool_name}`:ex.busy?`${ex.actions_used||0}/8 device actions · ${ex.elapsed_s||0}s / 90s`:'Existing device task in progress');
 const live=liveLine({status,mission,catalog,receipt:toolReceipt,voice,voiceProgress});
 const liveMeta=live?[live.actions?live.actions+(live.actions===1?' step':' steps'):'',Number.isFinite(live.elapsed)?Math.round(live.elapsed)+'s':''].filter(Boolean).join(' · '):'';
 const aspect=String(status?.target?.aspect||status?.aspect||710/1080);
 const connection=(status?.screen?'iPad live':'iPad offline')+' · ESP32 '+esp;
 const go=id=>{setView(id);setSettings(false);};
 const stopLabel='Stop what '+aiName+' is doing on the iPad (Esc)';

 const identity=<div className="identity">
  <Avatar name={aiName}/>
  <div className="identity-text"><b>{aiName}</b><small>{live?<>{live.model} · {live.phase}…</>:SUBTITLE}</small></div>
  <span className="status-dot" data-tone={deviceTone} data-live={!!live} role="img" aria-label={connection}/>
 </div>;

 return <AssistantRuntimeProvider runtime={runtime}><div className="app" data-voice={voice} data-running={!!busy} data-view={view} data-settings={settings}>
  {/* iPad and desktop: Muse-style sidebar */}
  <aside className="sidebar" aria-label="Sections">
   {identity}
   <nav className="side-nav">{VIEWS.map(v=><button key={v.id} className="side-tab" aria-current={view===v.id?'page':undefined} onClick={()=>go(v.id)}><v.icon size={20}/><span>{v.label}</span>
    {v.id==='feed'&&memory.discoveries.length>0&&<em>{memory.discoveries.length}</em>}{v.id==='library'&&memory.preferences.length>0&&<em>{memory.preferences.length}</em>}</button>)}</nav>
   <footer className="side-foot"><button className="round-btn" aria-label="Settings" onClick={()=>setSettings(true)}><Settings size={20}/></button><span className="side-conn">{connection}</span></footer>
  </aside>

  <div className="main">
   {/* phone: gear · identity · actions. wider: actions only, top right. Actions: Clear chat (chat view,
       when there is something to clear) and Stop iPad (only while a device task or exploration runs; Esc always stops). */}
   <header className="topbar">
    <button className="round-btn phone-only" aria-label="Settings" onClick={()=>setSettings(true)}><Settings size={20}/></button>
    <div className="phone-only topbar-identity">{identity}</div>
    <div className="topbar-actions">
     {view==='chat'&&<ClearChat runtime={runtime} voice={voice} busy={busy} name={aiName} hasCard={!!latest} onCleared={setClearedAt} onError={setError}/>}
     <button type="button" id="estop" className="stop-btn" hidden={!busy} onClick={stop} aria-keyshortcuts="Escape" aria-label={stopLabel} title={stopLabel}><Square size={12} fill="currentColor"/><span>Stop iPad</span></button>
    </div>
   </header>

   {(error||connectionError)&&<div role="alert" className="error-banner">{error||connectionError}<button onClick={()=>{setError('');setConnectionError('');}}>Dismiss</button></div>}

   {/* Chat: conversation and voice dock. Always mounted: voice.js owns its element ids. */}
   <section className="view view-chat" hidden={view!=='chat'} aria-label="Chat">
    <ThreadPrimitive.Root className="thread">
     <ThreadPrimitive.Viewport className="thread-viewport">
      <ThreadPrimitive.Empty><div className="greeting">
       <Avatar name={aiName} size="lg"/>
       <h1>{aiName}</h1>
       <p>{SUBTITLE}. Type or tap the mic.</p>
       <div className="chips">
        {ready&&!busy&&<button title="Starts from a public Safari page" onClick={()=>runtime.thread.append('Explore something surprising for me and bring back one discovery with a source.')}>Explore something for me</button>}
        <button onClick={()=>runtime.thread.append('What do you remember about me?')}>What do you remember?</button>
        <button onClick={()=>go('ipad')}>Show my iPad</button>
        <button onClick={()=>go('goals')}>Goals</button>
       </div>
      </div></ThreadPrimitive.Empty>
      <ThreadPrimitive.Messages components={{UserMessage,AssistantMessage:Message}}/>
      <VoiceTranscript/>
      {latest&&<div className="inline-source"><SourceCard item={latest}/></div>}
     </ThreadPrimitive.Viewport>

     <footer className="dock">
      {live&&<div className="live-line" role="status" aria-live="polite">
       <span className="live-dot" aria-hidden="true"/>
       <div className="live-text"><b>{live.model} · {live.phase}…</b>{live.thought&&<span>{live.thought}</span>}</div>
       {liveMeta&&<small>{liveMeta}</small>}
       <button onClick={()=>go('ipad')}>Watch</button>
      </div>}
      <div className="dock-status">{notice&&<p className="notice-line" role="alert">{notice}</p>}<VoiceStatus/></div>
      <div className="dock-row">
       <VoiceSideButtons/>
       <ComposerPrimitive.Root className="composer">
        <ComposerPrimitive.Input placeholder={'Message '+aiName} className="composer-input" aria-label={"Message "+aiName} rows={1}/>
        <ComposerPrimitive.Send className="send" aria-label="Send message"><ArrowUp size={18}/></ComposerPrimitive.Send>
       </ComposerPrimitive.Root>
       <div className="orb" data-state={voice}><span className="orb-label" aria-hidden="true">{ORB_LABEL[voice]}</span><span className="orb-ring" aria-hidden="true"/><VoiceOrbButtons/></div>
      </div>
     </footer>
    </ThreadPrimitive.Root>
   </section>

   {/* Feed: discoveries from real iPad visits */}
   <section className="view view-page" hidden={view!=='feed'} aria-label="Feed">
    <ViewHead title="Feed" note="Findings from real iPad visits, each with a checked quote and a source you can open."/>
    {!memory.discoveries.length?<div className="empty"><p>No discoveries yet.</p><small>A proposal only becomes a discovery after the source is checked.</small></div>:memory.discoveries.slice().reverse().map(item=><div key={item.id} className="discovery-row"><SourceCard item={item}/><button className="forget" onClick={()=>remove(item)}>Remove from memory</button></div>)}
   </section>

   {/* Goals: missions, app skills and task history */}
   <section className="view view-page" hidden={view!=='goals'} aria-label="Goals">
    <ViewHead title="Goals" note="Work toward a goal with App skills, or run one skill. Receipts stay on this Mac."/>
    <AppToolsPanel catalog={catalog} receipt={toolReceipt} ready={ready} onRefresh={refresh} mission={mission} busy={busy}/>
   </section>

   {/* Library: owner memory */}
   <section className="view view-page" hidden={view!=='library'} aria-label="Library">
    <ViewHead title="Library" note="You choose what stays. Stored locally on this Mac." action={<button className="round-btn tinted" aria-label="Add memory" onClick={add}><Plus size={20}/></button>}/>
    {editing&&<form className="memory-form group" onSubmit={e=>{e.preventDefault();save();}}><label htmlFor="memory-input">{editing.id?'Edit memory':'What should '+aiName+' remember?'}</label><textarea id="memory-input" autoFocus maxLength={500} value={draft} onChange={e=>setDraft(e.target.value)} placeholder="For example: I prefer stories about the ocean."/><div><button type="button" onClick={()=>setEditing(null)}>Cancel</button><button type="submit" disabled={saving||!draft.trim()}>{saving?'Saving…':'Save memory'}</button></div></form>}
    {!memory.preferences.length&&!editing&&<div className="empty"><p>Only what you ask me to remember.</p><small>Say “Remember that I like…” or tap + to add one.</small></div>}
    {memory.preferences.length>0&&<div className="group">{memory.preferences.map(m=><article className="memory-row" key={m.id}><div><p>{m.text}</p><small>Saved on request · {m.modality} · {new Date(m.created_at*1000).toLocaleDateString()}</small></div><button aria-label="Edit memory" onClick={()=>{setEditing(m);setDraft(m.text);}}><Pencil size={17}/></button><button aria-label="Forget memory" onClick={()=>remove(m)}><Trash2 size={17}/></button></article>)}</div>}
    {memory.forgotten?.length>0&&<details className="forgotten group"><summary>Recently forgotten ({memory.forgotten.length})</summary><p>Excluded from recall and topic selection. Restore anytime.</p>{memory.forgotten.map(m=><div key={m.id}><span>{m.text||m.claim}</span><button onClick={()=>restore(m)}>Restore</button></div>)}</details>}
   </section>

   {/* iPad: the live mirrored screen */}
   <section className="view view-page" hidden={view!=='ipad'} aria-label="iPad">
    <ViewHead title="iPad" note={mirrorAge(status)+' Mac coordinates. ESP32 provides Bluetooth input.'}/>
    <div className="device-frame" style={{'--aspect':aspect}}>{frame?<img src={frame} alt="Live iPad screen" onError={()=>setFrame('')}/>:<div className="screen-offline">{mirrorMessage(status)}</div>}</div>
    <div className="device-state"><span className={busy?'activity busy':'activity'}/>{live?<span><b>{live.model} · {live.phase}…</b>{live.thought&&<> {live.thought}</>}</span>:deviceState}</div>
    {busy&&<p className="progress">{progress}</p>}
    <div className="group">
     <div className="row static"><span className="row-label">Screen</span><span className="row-detail">{status?.screen?'Live':'Offline'}</span></div>
     <div className="row static"><span className="row-label">ESP32</span><span className="row-detail">{esp[0].toUpperCase()+esp.slice(1)}</span></div>
     <div className="row static"><span className="row-label">Calibration</span><span className="row-detail">{status?.calibrated?'Ready':'Not ready'}</span></div>
    </div>
   </section>

   {/* phone: floating pill tab bar */}
   <nav className="tabbar" aria-label="Sections">{VIEWS.map(v=><button key={v.id} aria-label={v.label} aria-current={view===v.id?'page':undefined} onClick={()=>go(v.id)}><v.icon size={22}/></button>)}</nav>
  </div>

  <div className="scrim" onClick={()=>setSettings(false)} aria-hidden="true"/>
  <Sheet open={settings} title="Settings" onClose={()=>setSettings(false)}>
   <div className="settings-id"><Avatar name={aiName} size="lg"/><div><b>{aiName}</b><small>{SUBTITLE}</small></div></div>
   <div className="group"><NameSetting/></div>
   <div className="group">
    <Row icon={Tablet} label="iPad screen" detail={status?.screen?'Live':'Offline'} onClick={()=>go('ipad')}/>
    <Row icon={ExternalLink} label="Open control console" href="/"/>
   </div>
   <div className="group">
    <div className="row static"><span className="row-label">Planner</span><span className="row-detail">{status?.models?.planner||'checking'}</span></div>
    <div className="row static"><span className="row-label">Control</span><span className="row-detail">{modelRoute(status).replace(/^Control: /,'')}</span></div>
    <div className="row static"><span className="row-label">Voice</span><span className="row-detail">Gemini Live audio</span></div>
   </div>
   <footer className="credit">Built with assistant-ui + Hermes memory · ESP32 iPhone Agent by Henry Yuan · MIT License</footer>
  </Sheet>
 </div><dialog id="zoom"/></AssistantRuntimeProvider>;
}
createRoot(document.getElementById('root')).render(<App/>);
