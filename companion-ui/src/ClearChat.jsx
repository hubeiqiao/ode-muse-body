import React,{useEffect,useRef,useState} from 'react';
import {MessageCircleX} from 'lucide-react';
import {api} from './api';

// Clear chat: wipes the visible conversation (assistant-ui thread + voice.js transcript rows) and the
// server's recent-conversation log (DELETE /api/companion/conversation). Library, Feed, Goals and voice
// task activity are separate stores and stay. Two taps: the first arms it, the second clears.

const CLEARED_KEY='ode.chatClearedAt';
// Seconds since epoch of the last clear; the inline discovery card in the chat only shows newer finds.
export function chatClearedAt(){try{return Number(localStorage.getItem(CLEARED_KEY))||0;}catch{return 0;}}
function markCleared(){const at=Date.now()/1000;try{localStorage.setItem(CLEARED_KEY,String(at));}catch{}return at;}

// voice.js flushes the last turns with a keepalive POST as a session ends; wait that out before clearing.
const VOICE_SETTLE_MS=2000,ARM_MS=4000;

function useThreadInfo(runtime){
 const read=()=>{const s=runtime.thread.getState();return {count:s.messages.length,running:s.isRunning};};
 const [info,setInfo]=useState(read);
 useEffect(()=>{const update=()=>setInfo(prev=>{const next=read();return prev.count===next.count&&prev.running===next.running?prev:next;});update();return runtime.thread.subscribe(update);},[runtime]);
 return info;
}
function useTranscriptRows(){
 const [rows,setRows]=useState(0);
 useEffect(()=>{
  const log=document.getElementById('voice-transcript');if(!log)return;
  const update=()=>setRows(log.children.length);update();
  const observer=new MutationObserver(update);observer.observe(log,{childList:true});
  return()=>observer.disconnect();
 },[]);
 return rows;
}

export default function ClearChat({runtime,voice,busy,name='Ode',hasCard=false,onCleared,onError}){
 const thread=useThreadInfo(runtime),rows=useTranscriptRows();
 const [armed,setArmed]=useState(false),[clearing,setClearing]=useState(false),[settling,setSettling]=useState(false);
 const wasVoice=useRef(false);
 useEffect(()=>{
  const active=voice!=='idle';
  if(active){wasVoice.current=true;setSettling(false);return;}
  if(!wasVoice.current)return;
  wasVoice.current=false;setSettling(true);const t=setTimeout(()=>setSettling(false),VOICE_SETTLE_MS);return()=>clearTimeout(t);
 },[voice]);
 useEffect(()=>{if(!armed)return;const t=setTimeout(()=>setArmed(false),ARM_MS);return()=>clearTimeout(t);},[armed]);
 const voiceOn=voice!=='idle'||settling;
 const blocked=voiceOn?'End the voice conversation to clear the chat':thread.running||busy?'Wait for '+name+' to finish, or stop it first':'';
 useEffect(()=>{if(blocked)setArmed(false);},[blocked]);
 if(!thread.count&&!rows&&!hasCard)return null;

 async function clear(){
  if(blocked||clearing)return;
  if(!armed){setArmed(true);return;}
  setClearing(true);
  try{
   await api('/api/companion/conversation',undefined,'DELETE');
   document.getElementById('voice-transcript')?.replaceChildren();
   runtime.thread.reset();
   onCleared?.(markCleared());
  }catch(e){onError?.('Chat could not be cleared: '+e.message);}
  finally{setClearing(false);setArmed(false);}
 }
 const label=blocked?'Clear chat. '+blocked:armed?'Tap again to clear the chat. Library, Feed and Goals stay.':'Clear chat';
 return <button type="button" className="clear-chat" data-armed={armed} disabled={!!blocked||clearing} onClick={clear}
  onBlur={()=>setArmed(false)}
  aria-label={label} title={blocked||(armed?'Tap again to clear. Library, Feed and Goals stay.':'Clear chat')}>
  <MessageCircleX size={20}/><span aria-hidden="true">{clearing?'Clearing…':'Clear chat?'}</span>
 </button>;
}
