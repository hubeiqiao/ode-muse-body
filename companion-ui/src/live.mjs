// Live "Muse Spark · planning…" line. Pure helpers over /api/status data so they stay testable.

// "muse-spark-1.3" -> "Muse Spark". Unknown or missing ids fall back to the raw id or "Muse Spark".
export function prettyModel(id){
 if(!id)return 'Muse Spark';
 const base=String(id).replace(/[-_ ]?(v?\d+(\.\d+)*|latest)$/i,'');
 const words=base.split(/[-_ ]+/).filter(Boolean);
 return words.length?words.map(w=>w[0].toUpperCase()+w.slice(1)).join(' '):String(id);
}

// Step thoughts look like "muse-spark-1.3: No icon visible, open Spotlight | Jev: precondition confirmed".
// Keep only the planner's own sentence.
export function cleanThought(text){
 return String(text||'').split(' | ')[0]
  .replace(/^[\w.-]+(?: backup)?:\s*/,'')
  .replace(/^no DO line in reply:\s*/i,'')
  .replace(/^THINK:\s*/i,'')
  .trim();
}

// {phase, thought, actions} for a running device agent.
export function agentLine(agent){
 const steps=Array.isArray(agent?.steps)?agent.steps:[];
 const last=steps.at(-1),action=String(last?.action||'');
 const phase=!last||action==='plan'||action.startsWith('replan')?'planning':action==='hiccup'?'rethinking':'thinking';
 const withThought=[...steps].reverse().find(s=>s.thought||s.reason);
 return {phase,thought:cleanThought(withThought?.thought||withThought?.reason),actions:steps.filter(s=>s.act_s!=null).length};
}

// One object for whatever is running, or null when idle. voiceProgress is the newest ESP32 voice row.
export function liveLine({status,mission,catalog,receipt,voice,voiceProgress}){
 const planner=prettyModel(status?.models?.planner);
 const ex=status?.exploration||{};
 if(status?.agent?.running){const a=agentLine(status.agent);return {model:planner,...a,thought:a.thought||voiceProgress||'',elapsed:status.agent.elapsed_s};}
 if(mission?.running){const step=mission.steps?.at(-1);return {model:prettyModel(catalog?.missions?.model||status?.models?.planner),phase:'working toward a goal',thought:step?.reason||mission.goal||'',actions:mission.steps_used||0,elapsed:mission.elapsed_s};}
 if(receipt?.running)return {model:planner,phase:'using '+(receipt.tool_name||'an app skill'),thought:voiceProgress||'',elapsed:receipt.elapsed_s};
 if(ex.busy)return {model:planner,phase:'exploring',thought:ex.proposal?.topic||'',actions:ex.actions_used||0,elapsed:ex.elapsed_s};
 if(status?.control_activity?.app_tool_running||voice==='working')return {model:planner,phase:'working',thought:voiceProgress||''};
 return null;
}
