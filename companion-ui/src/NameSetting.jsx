import React,{useState} from 'react';
import {Pencil} from 'lucide-react';
import {useAiNameState,saveAiName} from './aiName';
// Owner control for the AI's name (local only). Voice: "Your name is …" uses the same setter.
export default function NameSetting(){
 const s=useAiNameState();
 const [editing,setEditing]=useState(false),[draft,setDraft]=useState(''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 async function save(){
  if(!draft.trim()||saving)return;setSaving(true);setError('');
  try{await saveAiName(draft);setEditing(false);}catch(e){setError(e.message);}finally{setSaving(false);}
 }
 return <section className="name-setting" aria-label="My name">
  <div className="name-row"><div><h2>Name</h2><p>{s.name?<><strong>{s.name}</strong> · {s.source==='owner_setting'?'chosen by you':'default name'}</>:'Checking my name…'}</p><small>You can also say “Your name is …” in voice</small></div>
   {!editing&&<button onClick={()=>{setDraft(s.name);setError('');setEditing(true);}} disabled={!s.name}><Pencil size={16}/> Rename</button>}</div>
  {editing&&<form className="memory-form name-form" onSubmit={e=>{e.preventDefault();save();}}>
   <label htmlFor="ai-name-input">What should I be called?</label>
   <input id="ai-name-input" autoFocus maxLength={s.max_length||32} value={draft} onChange={e=>setDraft(e.target.value)} autoComplete="off" spellCheck={false}/>
   <small>1 to {s.max_length||32} letters, digits, spaces or hyphens. Stored locally on this Mac.</small>
   {error&&<p className="error" role="alert">{error}</p>}
   <div><button type="submit" disabled={saving||!draft.trim()||draft.trim()===s.name}>{saving?'Saving…':'Save name'}</button><button type="button" onClick={()=>setEditing(false)}>Cancel</button></div>
  </form>}
 </section>;
}
