"""One Live conversation, synthetic camera receipts only; no device or network."""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from hidagent import camera_conversation as cc
from hidagent.live_voice import ToolCall, VoiceControl
from test_camera_conversation import make, bridge_for, _mint


def normal():
    cam, _ = make()
    b, state, _ = bridge_for(cam)
    sid = 'continuous-camera-session'
    b.sessions[sid] = dict(expires=time.monotonic()+600, calls={}, paused=False,
                           cancelled_ids=set(), device_family='ipad')
    for method in ('launch_camera', 'flip_to_front', 'leave_camera'):
        setattr(cam.detector, method, AsyncMock())
    cam.detector.take_photo = AsyncMock(return_value={'status': 'photo_taken'})
    return b, cam, state, sid


def command(sid, name, turn=1, **kw):
    return ToolCall(session=sid, id=kw.pop('id', name), name=name,
                    utterance=kw.pop('utterance', name.replace('_', ' ')), owner_turn=turn, **kw)


@pytest.mark.parametrize('flag', ['0', '1'])
def test_normal_setup_offers_camera_actions_never_a_switch(monkeypatch, flag):
    monkeypatch.setenv('CAMERA_CONVERSATION', flag)
    b, cam, _, _ = normal()
    result, _ = _mint(monkeypatch, b)
    prompt = result['setup']['systemInstruction']['parts'][0]['text']
    names = {d['name'] for d in result['setup']['tools'][0]['functionDeclarations']}
    assert {'open_camera', 'flip_camera', 'take_photo', 'close_camera'} <= names
    assert not names & {'start_camera_conversation', 'return_to_device_mode', 'end_camera_conversation'}
    assert 'camera preview' in prompt and 'open_camera' in prompt
    assert 'camera mode' not in prompt.lower() and 'normal voice mode' not in prompt.lower()
    assert not cam.active()


def test_open_flip_photo_close_use_detector_windows_without_camera_session():
    async def scenario():
        b, cam, _, sid = normal()
        seen = []
        async def action(name, whitelist, result=None, **kw):
            assert cc._hid_window.get() == whitelist
            assert not cam.active() and 'camera' not in b.sessions[sid]
            seen.append(name)
            return result
        # AsyncMock side effects must themselves be async.
        async def launch(): return await action('open', cc.LAUNCH_WHITELIST)
        async def flip(): return await action('flip', cc.FLIP_WHITELIST)
        async def photo(**kw): return await action('photo', cc.PHOTO_WHITELIST, {'status':'photo_taken'}, **kw)
        async def leave(): return await action('close', cc.LEAVE_WHITELIST)
        cam.detector.launch_camera.side_effect = launch
        cam.detector.flip_to_front.side_effect = flip
        cam.detector.take_photo.side_effect = photo
        cam.detector.leave_camera.side_effect = leave
        for turn, (name, expected) in enumerate([('open_camera','camera_opened'), ('flip_camera','camera_flipped'),
                                                ('take_photo','photo_taken'), ('close_camera','camera_closed')], 1):
            r = await b.call(command(sid, name, turn))
            assert r['status'] == expected and r['terminal']
        assert seen == ['open','flip','photo','close']
        assert 'look' in (await b.call(command(sid, 'flip_camera', 2)))['instruction'].lower()
    asyncio.run(scenario())


def test_camera_calls_deduplicate_owner_turn_and_never_retry_uncertain_photo():
    async def scenario():
        b, cam, _, sid = normal()
        cam.detector.take_photo.return_value = {'status':'photo_uncertain'}
        first = await b.call(command(sid, 'take_photo'))
        assert first['status'] == 'photo_uncertain'
        assert await b.call(command(sid, 'take_photo', id='new-model-id')) == first
        cam.detector.take_photo.assert_awaited_once()
        # Repeated explicit owner flip requests may each toggle once.
        await b.call(command(sid, 'flip_camera', 2))
        await b.call(command(sid, 'flip_camera', 3, id='flip-again'))
        assert cam.detector.flip_to_front.await_count == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('condition', ['paused', 'busy', 'wrong_device', 'native_driver'])
def test_camera_actions_fail_closed(condition):
    async def scenario():
        b, cam, state, sid = normal()
        call = command(sid, 'take_photo')
        if condition == 'no_owner': call = command(sid, 'take_photo', utterance='')
        if condition == 'paused': b.sessions[sid]['paused'] = True
        if condition == 'busy': state['agent']['running'] = True
        if condition == 'wrong_device': state['target']['device'] = 'iphone'
        if condition == 'native_driver': b.driver_name = 'wda'
        r = await b.call(call)
        assert r['status'] in ('rejected', 'stopped', 'busy', 'not_ready')
        cam.detector.take_photo.assert_not_awaited()
    asyncio.run(scenario())


def test_stop_during_camera_action_cancels_input_without_ending_conversation():
    async def scenario():
        b, cam, _, sid = normal()
        entered = asyncio.Event()
        async def wait_for_stop():
            entered.set()
            await asyncio.Event().wait()
        cam.detector.launch_camera.side_effect = wait_for_stop
        call = asyncio.create_task(b.call(command(sid, 'open_camera')))
        await asyncio.wait_for(entered.wait(), 1)
        await b.interrupt(VoiceControl(session=sid, text='stop'))
        r = await asyncio.wait_for(call, 1)
        assert r['status'] == 'stopped'
        assert sid in b.sessions and not cam.active()
    asyncio.run(scenario())


@pytest.mark.parametrize('caption,expected', [('', 'Joe says hello.'), ('literal', 'hello from Joe')])
def test_photo_to_x_uses_same_session_without_handoff(caption, expected):
    async def scenario():
        b, cam, _, sid = normal()
        b.demos = type('Demos', (), {'busy':False, 'run':AsyncMock(return_value={'status':'awaiting_confirmation'})})()
        utterance = 'Post this photo on X' + (' with caption "hello from Joe"' if caption else ', describe the scene')
        r = await b.call(command(sid, 'ipad_demo', args={'action':'x_photo_draft', 'text':'Joe says hello.'}, utterance=utterance))
        assert r['status'] == 'awaiting_confirmation'
        args = b.demos.run.await_args.args
        assert args[0] == sid and args[-1].action == 'x_photo_draft' and args[-1].text == expected
        assert not b.camera_handoff.pending and not cam.active()
        cam.detector.take_photo.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('text', ['take a photo', 'flip the camera', 'close the camera', '拍照', '翻转相机', '关闭相机'])
def test_fresh_camera_request_resumes_after_stop(text):
    from hidagent.control_intent import is_action_intent
    assert is_action_intent(text)


def test_pending_camera_action_reserves_console_before_first_await():
    async def scenario():
        b, cam, _, sid = normal()
        entered, release = asyncio.Event(), asyncio.Event()
        async def launch():
            entered.set()
            await release.wait()
        cam.detector.launch_camera.side_effect = launch
        pending = asyncio.create_task(b.call(command(sid, 'open_camera')))
        await asyncio.sleep(0)  # call reserved, detector task has not necessarily started
        assert b._console_busy()
        second = await b.call(command(sid, 'flip_camera', 2))
        assert second['status'] == 'busy'
        cam.detector.flip_to_front.assert_not_awaited()
        release.set()
        assert (await pending)['status'] == 'camera_opened'
    asyncio.run(scenario())


def test_stop_before_detector_task_starts_returns_stopped():
    async def scenario():
        b, cam, _, sid = normal()
        pending = asyncio.create_task(b.call(command(sid, 'open_camera')))
        await asyncio.sleep(0)
        await b.interrupt(VoiceControl(session=sid, text='stop'))
        assert (await pending)['status'] == 'stopped'
        cam.detector.launch_camera.assert_not_awaited()
    asyncio.run(scenario())


def test_camera_whitelist_applies_without_a_camera_session():
    from hidagent.hid_lease import check_hid
    assert not cc.active_any()
    with cc.camera_hid_window(cc.PHOTO_WHITELIST):
        check_hid('move 1 2')
        check_hid('click')
        with pytest.raises(RuntimeError):
            check_hid('type forbidden')
    check_hid('type allowed_outside_window')  # fake lease check only, no hardware


def test_repeated_same_camera_request_joins_running_action():
    async def scenario():
        b, cam, _, sid = normal()
        release = asyncio.Event()
        async def launch():
            await release.wait()
        cam.detector.launch_camera.side_effect = launch
        first = asyncio.create_task(b.call(command(sid, 'open_camera')))
        await asyncio.sleep(0)
        second = asyncio.create_task(b.call(command(sid, 'open_camera', 2)))
        await asyncio.sleep(0)
        release.set()
        assert (await first)['status'] == 'camera_opened'
        assert (await second)['status'] == 'camera_opened'
        assert cam.detector.launch_camera.await_count == 1
    asyncio.run(scenario())
