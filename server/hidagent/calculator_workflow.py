"""Bounded iPad calculator arithmetic: activate, verify, type once, verify."""
import re
from .decision import DecisionUnavailable
from .jev_agent import scene_changed
from .shortcut_workflow import next_shortcut_actions


def calculator_goal(task):
 match=re.fullmatch(r'\s*(?:Open Calculator and calculate|Calculate)\s+(\d{1,6})\s*(times|plus|minus|[+*x×-])\s*(\d{1,6})(?:\. Verify the displayed answer and leave it visible\.)?\s*[.!]?\s*',task,re.I)
 if not match:
  match=re.fullmatch(r'\s*(?:打开计算器[，,。]?\s*)?计算\s*(\d{1,6})\s*(乘以|乘|加|减|[+*×-])\s*(\d{1,6})\s*[。！!]?\s*',task)
 if not match:return None
 left,op,right=match.groups();op={'times':'*','x':'*','×':'*','乘以':'*','乘':'*','plus':'+','加':'+','minus':'-','减':'-'}.get(op.lower(),op)
 a,b=int(left),int(right)
 expected=str(a*b if op=='*' else a+b if op=='+' else a-b)
 return {'expression':f'{a}{op}{b}','expected':expected}


async def next_calculator_actions(agent,goal):
 frame=agent.screen.frame
 history=[s for s in agent.steps if s.get('workflow')=='calculator']
 phases=[s.get('phase') for s in history]
 def emit(action,phase,seconds=0):
  if scene_changed(frame,agent.screen.frame):return wait('scene_changed',seconds)
  return [{**action,'workflow':'calculator','phase':phase,'_shot':frame,'think_s':seconds,
           'thought':'Checked calculator keyboard route: '+phase}]
 def wait(phase,seconds=0):
  if phases.count(phase)>=3:raise DecisionUnavailable('Calculator screen not verified; no expression resent')
  return [{'action':'wait','workflow':'calculator','phase':phase,'_shot':frame,'think_s':seconds}]
 if not phases:
  actions=await next_shortcut_actions(agent,{'app':'Calculator','text':None})
  if actions[0]['action']!='done':return actions
  return emit({'action':'key','combo':'esc'},'clear')
 if 'evaluate' in phases:
  phase='result';question=f'Is the ACTIVE FOREMOST Apple Calculator showing numerical RESULT {goal["expected"]}, with no different app or alert covering it? Require the actual result, not a prior expression or background page. Ignore digit grouping commas.'
 elif 'expression' in phases:
  phase='expression';question=f'Is the ACTIVE FOREMOST Apple Calculator visibly showing the complete UNEVALUATED expression {goal["expression"]}, with the correct operands and operation? Multiplication can appear as ×. No Search, other app input, or blocking alert.'
 else:
  phase='zero';question='Is the ACTIVE FOREMOST Apple Calculator showing a clear zero ready for hardware-keyboard arithmetic, with no Search, different app input, or blocking alert?'
 result=await agent.client.evaluate({'policy':'calculator_keyboard','goal':goal,'phase':phase},
     {'ready':{'type':'noul','instructions':question}},frame)
 agent.trace.write(frame,kind='calculator-keyboard-check',phase=phase,**result)
 if result['answers']['ready']['noul']<.99 or not result.get('approved',True):
  if phase=='zero' and 'clear_all' not in phases:
   clear=await agent.client.evaluate({'policy':'calculator_clear_all','goal':goal},{
       'ready':{'type':'noul','instructions':
           'Is the ACTIVE FOREMOST Apple Calculator visible with an AC (ALL CLEAR) key for resetting only its current expression, '
           'without Search, another app window or any dialog covering it? Not Clear History, C, a delete-backspace key, or data deletion.'},
       'target':{'type':'box','instructions':'Locate exactly the visible AC (All Clear) calculator key. Never a history clearing/deletion control, C, or backspace.'}},frame)
   agent.trace.write(frame,kind='calculator-clear-all-check',**clear)
   target=clear['answers'].get('target',{});box=target.get('box')
   if (clear['answers'].get('ready',{}).get('noul',0)>=.99 and target.get('confidence',0)>=.9
       and box and clear.get('approved',True)):
    return emit({'action':'tap','x':round((box['left']+box['right'])*500),
                 'y':round((box['top']+box['bottom'])*500),'precise':True},'clear_all',result['seconds']+clear['seconds'])
  return wait('verify_'+phase,result['seconds'])
 if phase=='result':return emit({'action':'done','answer':'Calculator result '+goal['expected']+' visibly verified.'},'verified',result['seconds'])
 if phase=='expression':return emit({'action':'key','combo':'enter'},'evaluate',result['seconds'])
 return emit({'action':'type','text':goal['expression']},'expression',result['seconds'])
