"""Versioned local-only health records between receiver JPEG frames."""
import json
import math

PREFIX=b'HIDAGENT_MIRROR_V1 '
HEARTBEAT_S=1.0
HEARTBEAT_TIMEOUT_S=4.0
MAX_RECORD=1024

def record(health):
 return PREFIX+json.dumps(health,separators=(',',':'),allow_nan=False).encode()+b'\n'

def decode(raw):
 value=json.loads(raw)
 required={'version','process_running','frame_ready','peer_connected','source_epoch','source_frame_age_s'}
 if not isinstance(value,dict) or not required.issubset(value):raise ValueError('invalid mirror status')
 if type(value.get('version')) is not int or value.get('version')!=1 or type(value.get('process_running')) is not bool or type(value.get('frame_ready')) is not bool:
  raise ValueError('invalid mirror status')
 peer=value.get('peer_connected')
 if peer is not None and type(peer) is not bool:raise ValueError('invalid mirror peer')
 epoch=value.get('source_epoch')
 if not isinstance(epoch,str) or not epoch or len(epoch)>100:raise ValueError('invalid mirror epoch')
 age=value.get('source_frame_age_s')
 if age is not None and (type(age) not in (int,float) or not math.isfinite(age) or age<0):raise ValueError('invalid mirror age')
 reason=value.get('source_offline_reason')
 if reason is not None and (reason not in ('client_feedback_timeout','ntp_no_route') or peer is not False):
  raise ValueError('invalid mirror offline reason')
 return {**{key:value[key] for key in ('version','process_running','frame_ready','peer_connected','source_epoch','source_frame_age_s')},
         'source_offline_reason':reason}
