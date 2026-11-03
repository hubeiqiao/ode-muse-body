"""Sequential physical-device trials; Home reset is outside each measured task."""
import sys,time,json,subprocess,urllib.request
from pathlib import Path
BASE='http://127.0.0.1:8894/api/'
SCRIPT=Path(__file__).with_name('run_e2e.py')
cases=json.loads(Path(sys.argv[1]).read_text())
for name,goal in cases:
    r=urllib.request.Request(BASE+'key',data=json.dumps({'text':'cmd+h'}).encode(),headers={'Content-Type':'application/json'})
    urllib.request.urlopen(r,timeout=10).read()
    r=urllib.request.Request(BASE+'key',data=json.dumps({'text':'esc'}).encode(),headers={'Content-Type':'application/json'})
    urllib.request.urlopen(r,timeout=10).read()
    time.sleep(.6)
    result=subprocess.run([sys.executable,str(SCRIPT),name,goal],check=True)
