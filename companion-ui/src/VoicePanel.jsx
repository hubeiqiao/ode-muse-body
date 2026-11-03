import React,{memo,useEffect,useState} from 'react';
import {Mic,Camera,Square} from 'lucide-react';
// Static markup only: /static/voice.js owns these elements after load (hidden, disabled,
// text and transcript rows). The components take no props, so React never re-renders them
// and never fights voice.js over attributes. They are split so the controls can sit in the
// dock and the transcript can sit in the conversation, but all of them mount with the page.

// Orb buttons: start (idle) and end (active). voice.js toggles voice-start.disabled and voice-end.hidden.
export const VoiceOrbButtons=memo(function VoiceOrbButtons(){
 return <>
  <button type="button" id="voice-start" className="orb-start" aria-label="Start voice"><Mic size={22}/></button>
  <button type="button" id="voice-end" className="orb-end" hidden aria-label="Stop voice"><Square size={18} fill="currentColor"/></button>
 </>;
},()=>true);

// Secondary dock buttons. voice.js sets voice-mute's text, so it holds plain text only.
export const VoiceSideButtons=memo(function VoiceSideButtons(){
 return <>
  <button type="button" id="voice-camera-start" className="dock-round" hidden aria-label="Open camera" title="Open camera"><Camera size={20}/></button>
  <button type="button" id="voice-mute" className="dock-pill" hidden>Mute</button>
 </>;
},()=>true);

export const VoiceStatus=memo(function VoiceStatus(){
 return <>
  <p id="voice-status" className="voice-status" role="status" aria-live="polite">Checking voice…</p>
 </>;
},()=>true);

// voice.js appends <p data-role="You|Gemini|ESP32"><b>Role: </b>text</p> rows here; CSS draws them as chat.
export const VoiceTranscript=memo(function VoiceTranscript(){
 return <div id="voice-transcript" className="voice-transcript" aria-label="Voice transcript" aria-live="polite"/>;
},()=>true);

// Reads the voice state that voice.js already exposes in the DOM, without changing it:
// body[data-voice-active], #voice-status text and the newest #voice-transcript row.
// Returns {state, notice, progress}; notice is a status line worth showing (an error or warning).
const NOTICE=/unavailable|could not|not confirmed|Use Emergency|不可用|未确认|紧急停止/i;
export function useVoiceState(){
 const [view,setView]=useState({state:'idle',notice:'',progress:''});
 useEffect(()=>{
  let lastRole='',lastAt=0,timer=0,progress='';
  const read=()=>{
   const active=document.body.dataset.voiceActive==='true';
   const text=document.getElementById('voice-status')?.textContent||'';
   let next='idle';
   if(active){
    if(/Connecting|连接/.test(text))next='connecting';
    else if(/Controlling|Stopping|操作手机|停止手机/.test(text))next='working';
    else if(lastRole==='Gemini'&&Date.now()-lastAt<1600)next='speaking';
    else next='listening';
   }
   const notice=NOTICE.test(text)?text:'';
   const p=active?progress:'';
   setView(v=>v.state===next&&v.notice===notice&&v.progress===p?v:{state:next,notice,progress:p});
  };
  // progress: the newest ESP32 row (voice.js device progress) without its "ESP32:" label.
  const rows=new MutationObserver(()=>{const log=document.getElementById('voice-transcript'),row=log?.lastElementChild;lastRole=row?.dataset.role||'';lastAt=Date.now();
   const esp=log?[...log.querySelectorAll('p[data-role="ESP32"]')].at(-1):null;progress=esp?esp.textContent.replace(/^\s*ESP32:\s*/,'').trim():'';
   read();clearTimeout(timer);timer=setTimeout(read,1700);});
  const other=new MutationObserver(read);
  const watch=()=>{
   const t=document.getElementById('voice-transcript'),s=document.getElementById('voice-status');
   if(t)rows.observe(t,{childList:true,subtree:true,characterData:true});
   if(s)other.observe(s,{childList:true,subtree:true,characterData:true});
   other.observe(document.body,{attributes:true,attributeFilter:['data-voice-active']});
  };
  watch();read();
  return()=>{rows.disconnect();other.disconnect();clearTimeout(timer);};
 },[]);
 return view;
}
