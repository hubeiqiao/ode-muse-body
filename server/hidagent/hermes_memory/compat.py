"""Embedding boundary for the unmodified Hermes storage algorithms."""
import fcntl
import os
import tempfile
from contextvars import ContextVar
from pathlib import Path
msvcrt=None
memory_dir=ContextVar('companion_memory_dir')
def get_memory_dir():return memory_dir.get()
def mkdir_under_hermes_home(path):
    path=Path(path);base=get_memory_dir().resolve()
    if path.resolve()!=base:raise ValueError('Memory path escaped device profile')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
def atomic_write_text(path,text,tmp_prefix='.mem_'):
    fd,name=tempfile.mkstemp(prefix=tmp_prefix,dir=Path(path).parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
def _memory_target_error(store,target,operations=None):
    if target not in ('user','memory'):return {'success':False,'error':'Unknown memory target'}
    return None
