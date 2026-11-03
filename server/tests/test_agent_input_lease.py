import asyncio
from types import SimpleNamespace
from unittest.mock import Mock,AsyncMock
from hidagent.agent import Agent
from hidagent.phone import Phone


def test_stop_revokes_late_planner_even_if_provider_swallows_cancel(tmp_path):
 async def run():
  entered=asyncio.Event();finish=asyncio.Event();sent=[]
  async def send(command):sent.append(command)
  phone=Phone(SimpleNamespace(links=1,connected=True,send=send),tmp_path/'map.json')
  class LatePlanner(Agent):
   async def _replan(self,stuck=False):
    entered.set()
    try:await finish.wait()
    except asyncio.CancelledError:await finish.wait()
   async def _step(self):await self.phone.key('cmd+space');return True
  a=LatePlanner(phone,SimpleNamespace(live=True),None,None,Mock(),max_steps=1)
  a.start('Open public search');await entered.wait();a.stop();finish.set();await asyncio.wait_for(a._job,1)
  assert sent==[]
  assert a.steps[-1]['action']=='stopped'
 asyncio.run(run())


def test_stop_between_hid_stages_blocks_click_but_allows_release(tmp_path):
 async def run():
  entered=asyncio.Event();finish=asyncio.Event();sent=[]
  async def send(command):
   sent.append(command)
   if command=='move 100 0':
    entered.set()
    try:await finish.wait()
    except asyncio.CancelledError:await finish.wait()
  phone=Phone(SimpleNamespace(links=1,connected=True,send=send),tmp_path/'map.json')
  class LateMove(Agent):
   async def _replan(self,stuck=False):pass
   async def _step(self):
    try:
     await self.phone._send('move 100 0');await self.phone._send('click');return True
    finally:await self.phone.release_input()
  a=LateMove(phone,SimpleNamespace(live=True),None,None,Mock(),max_steps=1)
  a.start('Move to public Search');await entered.wait();a.stop();finish.set();await asyncio.wait_for(a._job,1)
  assert sent==['move 100 0','release']
  assert a.steps[-1]['action']=='stopped'
 asyncio.run(run())


def test_explicit_new_task_can_act_after_prior_task_stopped(tmp_path):
 async def run():
  sent=[]
  async def send(command):sent.append(command)
  phone=Phone(SimpleNamespace(links=1,connected=True,send=send),tmp_path/'map')
  phone.ensure_keyboard_live=AsyncMock()
  class Ready(Agent):
   async def _replan(self,stuck=False):pass
   async def _step(self):await self.phone.key('cmd+space');return True
  a=Ready(phone,SimpleNamespace(live=True),None,None,Mock(),max_steps=1)
  a.stop();a.start('New explicit public task');await a._job
  assert sent==['key cmd+space']
 asyncio.run(run())


def test_inherited_background_input_expires_with_completed_task(tmp_path):
 async def run():
  trigger=asyncio.Event();sent=[];children=[]
  async def send(command):sent.append(command)
  phone=Phone(SimpleNamespace(links=1,connected=True,send=send),tmp_path/'map')
  async def late():await trigger.wait();await phone._send('click')
  class Spawned(Agent):
   async def _replan(self,stuck=False):pass
   async def _step(self):children.append(asyncio.create_task(late()));return True
  a=Spawned(phone,SimpleNamespace(live=True),None,None,Mock(),max_steps=1)
  a.start('One bounded public task');await a._job;trigger.set()
  result=await asyncio.gather(*children,return_exceptions=True)
  assert isinstance(result[0],asyncio.CancelledError) and not sent
 asyncio.run(run())


def test_nested_input_lease_cannot_bypass_revoked_parent():
 from hidagent.hid_lease import leased_input,check_hid
 revoked=False
 def parent():
  if revoked:raise asyncio.CancelledError()
 with leased_input(parent):
  with leased_input(lambda:None):
   check_hid('move 1 0');revoked=True
   try:check_hid('click')
   except asyncio.CancelledError:pass
   else:raise AssertionError('revoked parent was bypassed')
   check_hid('release')
