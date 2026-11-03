import {useSyncExternalStore} from 'react';
import {api,get} from './api';
// The AI's name is the owner's choice (GET/PUT /api/companion/name), default Ode on the server.
// A tiny store so static panels can show it without re-rendering (VoicePanel is owned by voice.js).
const TITLE=' · Your iPad companion';
let state={name:'',source:'',default:'',max_length:32};
const listeners=new Set();
function publish(next){if(next.name===state.name&&next.source===state.source)return;state=next;if(state.name)document.title=state.name+TITLE;listeners.forEach(l=>l());}
export const subscribe=l=>{listeners.add(l);return()=>listeners.delete(l);};
export const useAiNameState=()=>useSyncExternalStore(subscribe,()=>state);
export const useAiName=()=>useAiNameState().name||'your companion';
export async function loadAiName(){publish(await get('/api/companion/name'));return state;}
export async function saveAiName(name){publish(await api('/api/companion/name',{name},'PUT'));return state;}
export function AiName(){return useAiName();}
