"""Offline official twitter-text rules. No API, clipboard or device input."""
import json,os,shutil,subprocess
from pathlib import Path
from .decision import DecisionUnavailable
SCRIPT=Path(__file__).resolve().parents[2]/'tools/twitter-text-validator/check.cjs'
def measure_reply(text):
    if not isinstance(text,str) or len(text)>1500:raise DecisionUnavailable('Reply length input outside bounded limit')
    node=shutil.which('node')
    if not node:raise DecisionUnavailable('Official reply-length validator unavailable; no guessed count used')
    try:
        # Never inherit model credentials, NODE_OPTIONS or unrelated process secrets.
        result=subprocess.run([node,str(SCRIPT)],input=json.dumps({'text':text},ensure_ascii=False).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=3,check=True,env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin','LANG':'en_US.UTF-8'})
        data=json.loads(result.stdout)
        if type(data.get('valid')) is not bool or type(data.get('weighted_length')) is not int or not 0<=data['weighted_length']<=4000 or data.get('remaining')!=280-data['weighted_length']:raise ValueError()
        return data
    except (ValueError,OSError,subprocess.SubprocessError):raise DecisionUnavailable('Official reply-length check failed; no guessed count or input sent') from None
