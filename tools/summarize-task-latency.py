import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(description='Offline attribution of an existing ESP32 task trace. No network or device input.')
p.add_argument('trace',type=Path);p.add_argument('--output',type=Path);p.add_argument('--voice-report',type=Path)
a=p.parse_args();rows=[json.loads(s) for s in (a.trace/'steps.jsonl').read_text().splitlines() if s.strip()]
rounds=[r for r in rows if r.get('kind')=='round_timing'];checks=[r for r in rows if r.get('kind') in ('shortcut-check','source-note-input-check','source-note-check')]
planning=sum(r.get('think_s',0) for r in rows if r.get('kind')=='step' and r.get('action')=='plan')
parts={'initial_planning_s':planning,**{k:sum(r.get(k,0) for r in rounds) for k in ('decision_s','action_s','observe_s')}}
report={'scope':'offline attribution of recorded task, not a new device run','trace':str(a.trace),'non_overlapping_parts':{k:round(v,4) for k,v in parts.items()},'judge_detail_included_in_planning_and_decision':{'calls':len(checks),'fallbacks':sum(bool(r.get('fallback_used')) for r in checks),'primary_s':round(sum(r.get('primary_s',r.get('seconds',0)) for r in checks),4),'backup_s':round(sum(r.get('backup_s',0) for r in checks),4)},'warning':'Judge detail overlaps planning/decision. Do not add it to non-overlapping parts. Historical screenshots are not live readiness.'}
if a.voice_report:
 voice=json.loads(a.voice_report.read_text());elapsed=voice.get('backendElapsedS');report.update(backend_s=elapsed,voice_total_s=voice.get('completedMs',0)/1000,voice_before_backend_s=round(voice.get('completedMs',0)/1000-elapsed,4),unattributed_backend_s=round(elapsed-sum(parts.values()),4))
text=json.dumps(report,indent=2);print(text)
if a.output:a.output.write_text(text+'\n')
