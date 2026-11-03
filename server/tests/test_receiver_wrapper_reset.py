import os,shlex,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('caller_timeout',[0,30])
def test_ipad_wrapper_does_not_override_callers_explicit_reset(tmp_path,caller_timeout):
 capture=tmp_path/'args';fake=tmp_path/'capture.sh';fake.write_text('#!/bin/sh\nprintf \'%s\\0\' "$@" > "$CAPTURE_PATH"\n');fake.chmod(0o700)
 wrapper=ROOT/'scripts/uxplay-ipad.sh'
 subprocess.run(['/bin/sh',str(wrapper),'-n','iPad Agent','-reset',str(caller_timeout),'-vs','fake sink ! jpegenc'],env={**os.environ,'CAPTURE_PATH':str(capture),'UXPLAY_BIN':str(fake)},check=True)
 args=capture.read_bytes().decode().rstrip('\0').split('\0')
 resets=[args[i+1] for i,x in enumerate(args[:-1]) if x=='-reset']
 assert resets==[str(caller_timeout)]
 assert args[args.index('-n')+1]=='iPad Agent'
 assert args[args.index('-vs')+1]=='fake sink ! jpegenc'
 assert args[args.index('-vd')+1]=='avdec_h264 max-threads=1 thread-type=slice'
 assert '-m' not in args
