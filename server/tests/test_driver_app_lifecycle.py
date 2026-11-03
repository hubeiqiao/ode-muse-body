"""Execute the real lifespan AST against fakes; never import app or bind sockets."""
import ast
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize('driver', ['esp32', 'android', 'wda'])
def test_native_startup_never_starts_esp32_listener_or_mirroring(driver, monkeypatch):
    monkeypatch.setenv('DEVICE_DRIVER', driver)
    tree = ast.parse((Path(__file__).parents[1] / 'hidagent/app.py').read_text())
    lifespan = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'lifespan')
    device = SimpleNamespace(serve=AsyncMock(return_value=Mock()), keep_awake=AsyncMock())
    screen = Mock()
    executor = SimpleNamespace(stop=Mock(), busy=False)
    adapter = SimpleNamespace(aclose=AsyncMock())
    stop = AsyncMock()
    env = dict(os=os, asyncio=asyncio, asynccontextmanager=asynccontextmanager,
               device=device, screen=screen, agent=SimpleNamespace(running=False, actor=None),
               manual_lock=asyncio.Lock(), exploration=SimpleNamespace(busy=False), camera_active_any=lambda: False,
               local_text=None, receipt_journal=Mock(), app_tools=SimpleNamespace(flush_history=AsyncMock()),
               agent_stop=stop, voice_bridge=SimpleNamespace(driver_name=driver, driver_executor=executor, driver=adapter))
    exec(compile(ast.Module(body=[lifespan], type_ignores=[]), '<real-lifespan>', 'exec'), env)
    async def scenario():
        async with env['lifespan'](None): pass
    asyncio.run(scenario())
    if driver == 'esp32':
        assert device.serve.await_count == 1 and screen.start.call_count == 1
    else:
        assert device.serve.await_count == 0 and screen.start.call_count == 0
        assert device.keep_awake.await_count == 0 and stop.await_count == 0
        assert executor.stop.call_count == 1
        adapter.aclose.assert_awaited_once()


def test_explicit_owner_memory_directory_keeps_device_runtime_separate(tmp_path, monkeypatch):
    monkeypatch.setenv('VOICE_MEMORY_DIR', str(tmp_path / 'owner-memory'))
    tree = ast.parse((Path(__file__).parents[1] / 'hidagent/app.py').read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and
                      any(isinstance(t, ast.Name) and t.id == 'companion_memory' for t in n.targets))
    configure = next(n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and
                     isinstance(n.value.func, ast.Name) and n.value.func.id == 'configure_ai_name_settings')
    chosen = []
    env = dict(os=os, Path=Path, DATA_DIR=tmp_path / 'isolated-device-runtime',
               CompanionMemory=lambda path: SimpleNamespace(path=path), configure_ai_name_settings=chosen.append)
    exec(compile(ast.Module(body=[assignment, configure], type_ignores=[]), '<real-memory-config>', 'exec'), env)
    assert env['companion_memory'].path == tmp_path / 'owner-memory'
    assert chosen == [tmp_path / 'owner-memory' / 'settings.json']
    assert env['DATA_DIR'] == tmp_path / 'isolated-device-runtime'
