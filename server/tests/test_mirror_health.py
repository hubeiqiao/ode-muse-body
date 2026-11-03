"""Real local sockets cover receiver/follower health without touching AirPlay."""
import socket
import threading
import time
from types import SimpleNamespace
from hidagent import receiver
from hidagent.screen import Screen

FRAME=b'\xff\xd8public-test-frame\xff\xd9'

def until(test,timeout=3):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if test():return
  time.sleep(.01)
 assert test()


def pair():
 source=SimpleNamespace(frame=FRAME,updated=time.time(),live=True)
 listener=socket.create_server(('127.0.0.1',0))
 screen=Screen(stream=f'127.0.0.1:{listener.getsockname()[1]}')
 def serve():
  conn,_=listener.accept();receiver._feed(source,conn)
 threading.Thread(target=serve,daemon=True).start();screen.start()
 return source,screen,listener


def test_still_connected_source_does_not_become_offline_just_from_frame_age():
 source,screen,listener=pair()
 try:
  until(lambda:screen.frame==FRAME)
  screen.updated=time.time()-120
  assert screen.live
 finally:screen.stop();listener.close()


def test_remote_disconnect_is_reported_without_tearing_down_local_transport():
 source,screen,listener=pair()
 try:
  until(lambda:screen.frame==FRAME)
  original=screen._sock
  source.live=False
  until(lambda:screen.frame is None)
  assert screen._sock is original
  assert screen.mirror_health()['state']=='source_disconnected'
  # A new session may not reuse the previous JPEG.
  source.live=True
  time.sleep(.12)
  assert screen.frame is None and not screen.live
  source.updated=time.time();source.frame=b'\xff\xd8new-session\xff\xd9'
  until(lambda:screen.frame==source.frame)
  assert screen.live
 finally:screen.stop();listener.close()


def status(**overrides):
 return {'version':1,'process_running':True,'frame_ready':True,'peer_connected':True,
         'source_epoch':'test-receiver:video1','source_frame_age_s':120,**overrides}


def test_fragmented_health_never_counts_as_a_new_picture_and_jpeg_payload_is_opaque():
 from hidagent.mirror_stream import record,PREFIX
 screen=Screen(stream='127.0.0.1:1');screen._sock=object()
 jpeg=b'\xff\xd8'+PREFIX+b'{not a status}\n\xff\xd9'
 chunks=iter(bytes([b]) for b in record(status())+jpeg+record(status(source_frame_age_s=121)))
 screen._consume(lambda:next(chunks,b''),health_records=True)
 assert screen.frame==jpeg and screen.live
 assert screen.mirror_health()['source_frame_age_s']==121
 updated=screen.updated
 chunks=iter([record(status(source_frame_age_s=122)),b''])
 screen._consume(lambda:next(chunks),health_records=True)
 assert screen.updated==updated


def test_disconnected_status_rejects_later_cached_frame_until_ready():
 from hidagent.mirror_stream import record
 screen=Screen(stream='127.0.0.1:1');screen._sock=object()
 chunks=iter([record(status(peer_connected=False,frame_ready=False))+FRAME,b''])
 screen._consume(lambda:next(chunks),health_records=True)
 assert screen.frame is None and not screen.live


def test_video_process_epoch_change_revokes_previous_picture():
 from hidagent.mirror_stream import record
 screen=Screen(stream='127.0.0.1:1');screen._sock=object()
 chunks=iter([record(status())+FRAME,record(status(source_epoch='video2')),b''])
 screen._consume(lambda:next(chunks),health_records=True)
 assert screen.frame is None and not screen.live


def test_unknown_peer_does_not_turn_an_old_cache_into_live():
 from hidagent.mirror_stream import record
 screen=Screen(stream='127.0.0.1:1');screen._sock=object()
 chunks=iter([record(status(peer_connected=None))+FRAME,b''])
 screen._consume(lambda:next(chunks),health_records=True)
 assert not screen.live and screen.mirror_health()['state']=='source_session_unknown'


def test_receiver_heartbeat_loss_reconnects_only_consumer_socket(monkeypatch):
 import hidagent.screen as module
 from hidagent.mirror_stream import record
 monkeypatch.setattr(module,'HEARTBEAT_TIMEOUT_S',.1)
 listener=socket.create_server(('127.0.0.1',0));listener.settimeout(3)
 screen=Screen(stream=f'127.0.0.1:{listener.getsockname()[1]}');screen.start()
 one,_=listener.accept()
 try:
  one.sendall(record(status())+FRAME)
  until(lambda:screen.frame==FRAME)
  until(lambda:screen.mirror_health()['state']=='receiver_heartbeat_lost')
  assert not screen.live
  two,_=listener.accept()  # follower reconnects to the same receiver port
  try:
   assert screen.frame is None
   two.sendall(record(status())+b'\xff\xd8recovered\xff\xd9')
   until(lambda:screen.frame==b'\xff\xd8recovered\xff\xd9')
  finally:two.close()
 finally:screen.stop();one.close();listener.close()


def test_legacy_still_stream_is_not_forcibly_reconnected(monkeypatch):
 import hidagent.screen as module
 monkeypatch.setattr(module,'HEARTBEAT_TIMEOUT_S',.01)
 listener=socket.create_server(('127.0.0.1',0));screen=Screen(stream=f'127.0.0.1:{listener.getsockname()[1]}');screen.start()
 conn,_=listener.accept()
 try:
  conn.sendall(FRAME);until(lambda:screen.frame==FRAME)
  original=screen._sock;time.sleep(1.1)
  assert screen._sock is original and screen.live
  assert screen.mirror_health()['health_protocol'] is None
 finally:screen.stop();conn.close();listener.close()


def test_invalid_health_is_bounded_and_fails_closed():
 import pytest
 from hidagent.mirror_stream import decode,PREFIX,record
 import json
 for bad in ({},status(version=True),status(peer_connected='yes'),status(source_frame_age_s=float('nan')),status(source_epoch='x'*101)):
  with pytest.raises(ValueError):decode(json.dumps(bad))
 for payload in (PREFIX+b'x'*1025,record(status())+PREFIX+b'{broken}\n'):
  screen=Screen(stream='127.0.0.1:1');chunks=iter([payload,b''])
  with pytest.raises(ValueError):screen._consume(lambda:next(chunks),health_records=True)


def test_receiver_termination_reaps_only_its_isolated_video_child(tmp_path):
 import os,subprocess,sys,signal
 fake=tmp_path/'fake-video'
 fake.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n');fake.chmod(0o755)
 listener=socket.create_server(('127.0.0.1',0));port=listener.getsockname()[1];listener.close()
 env={**os.environ,'UXPLAY':str(fake),'HIDAGENT_DATA':str(tmp_path),'MIRROR_PORT':str(port),'AIRPLAY_NAME':'Isolated QA receiver'}
 process=subprocess.Popen([sys.executable,'-m','hidagent.receiver'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 child=None
 try:
  until(lambda:(tmp_path/'uxplay.pid').exists())
  child=int((tmp_path/'uxplay.pid').read_text())
  process.terminate();assert process.wait(timeout=5)==0
  def gone():
   try:os.kill(child,0)
   except ProcessLookupError:return True
   return False
  until(gone)
 finally:
  if process.poll() is None:process.kill();process.wait()
  if child:
   try:os.kill(child,signal.SIGTERM)
   except ProcessLookupError:pass
