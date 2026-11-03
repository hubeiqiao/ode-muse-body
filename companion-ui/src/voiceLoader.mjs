// Loads the tested browser voice controller from the '/' console exactly once.
// voice.js binds DOM ids at load time, so it must run after VoicePanel is in the DOM.
export const VOICE_SCRIPTS=['/static/voice-intents.js','/static/voice.js'];
export function loadVoiceScripts(doc=document,win=window){
 if(win.__miloVoiceLoad)return win.__miloVoiceLoad;
 win.__miloVoiceLoad=VOICE_SCRIPTS.reduce((chain,src)=>chain.then(()=>new Promise((resolve,reject)=>{
  if(doc.querySelector(`script[data-milo-voice="${src}"]`))return resolve();
  const script=doc.createElement('script');
  script.src=src+'?milo=1';script.dataset.miloVoice=src;
  script.onload=resolve;script.onerror=()=>reject(new Error('Could not load '+src));
  doc.body.append(script);
 })),Promise.resolve());
 return win.__miloVoiceLoad;
}
