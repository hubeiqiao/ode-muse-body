"""Offline ownership contracts at every model-prompt boundary."""
import ast
import asyncio
import importlib
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parents[1] / 'hidagent'
# Transport adapters forward caller prompts, including neutral judge prompts.
TRANSPORTS = {'llm.py', 'meta_model.py'}
JUDGES = {'vision_judge.py', 'scenario_tools.py', 'public_browser.py', 'exploration_agent.py', 'inventory_note.py',
          'app_result_reader.py', 'grok_action.py', 'grok_preview.py'}


def prompt_calls():
    for path in sorted(ROOT.glob('*.py')):
        if path.name in TRANSPORTS:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'chat':
                # This FastAPI handler calls Companion.chat(body), not a model.
                if path.name == 'companion.py' and isinstance(node.func.value, ast.Name) and node.func.value.id == 'companion':
                    continue
                yield path.name, node


CALLS = list(prompt_calls())


def test_mcp_instructions_include_shared_ownership_without_starting_service():
    from hidagent.mcp import create_server
    instructions = create_server('http://offline.invalid').instructions
    assert 'This iPad is your own device - your body.' in instructions
    identity = importlib.import_module('hidagent.identity')
    assert identity.DEVICE_OWNERSHIP in instructions
    assert 'Only act on the user\'s requested device task.' in instructions


@pytest.mark.parametrize('filename,node', CALLS, ids=[f'{p}:{n.lineno}' for p, n in CALLS])
def test_every_model_prompt_boundary_binds_ownership(filename, node):
    expected = 'judge_prompt' if filename in JUDGES else 'device_prompt'
    prompt = node.args[0]
    assert isinstance(prompt, ast.Call) and isinstance(prompt.func, ast.Name) and prompt.func.id == expected, (
        f'{filename}:{node.lineno} must bind ownership with {expected}')
    identity = importlib.import_module('hidagent.identity')
    # Evaluate the actual caller expression, preserving its role/safety text.
    # Locally assembled context is synthetic; no model or device is accessed.
    scope = {name.id: 'Original role and criteria.' for name in ast.walk(prompt) if isinstance(name, ast.Name)}
    scope.update(device_prompt=identity.device_prompt, judge_prompt=identity.judge_prompt, observation=None)
    original = eval(compile(ast.Expression(prompt.args[0]), filename, 'eval'), {'__builtins__': {}}, scope)
    actual = eval(compile(ast.Expression(prompt), filename, 'eval'), {'__builtins__': {}}, scope)
    constant = identity.DEVICE_JUDGE_OWNERSHIP if filename in JUDGES else identity.DEVICE_OWNERSHIP
    assert actual == original + '\n' + constant
    assert getattr(identity, expected)(actual) == actual


@pytest.mark.parametrize('builder', ['voice_context', 'camera_context'])
@pytest.mark.parametrize('family', ['ipad', 'iphone'])
def test_voice_builders_include_shared_ownership_without_memory(builder, family):
    from hidagent import live_voice
    system, _ = getattr(live_voice, builder)({'target': {'device': family}, 'models': {}})
    assert 'This iPad is your own device - your body.' in system
    identity = importlib.import_module('hidagent.identity')
    assert identity.DEVICE_OWNERSHIP in system
    assert "user's real" not in system


def test_jev_judge_preamble_preserves_questions_and_state():
    from hidagent.decision import Jev
    questions = {'ready': {'type': 'noul', 'instructions': 'Is the editor visible?'}}
    async def run():
        sent = []
        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'answers': {'ready': {'type': 'noul', 'noul': 1}}})
        client = Jev('synthetic-key', transport=httpx.MockTransport(handle))
        try:
            await client.evaluate({'policy': 'visible_only'}, questions)
        finally:
            await client.close()
        assert 'The iPad belongs to the AI agent; apply the existing evidence and verdict criteria unchanged.' in sent[0]['questions']['ready']['instructions']
        identity = importlib.import_module('hidagent.identity')
        assert sent[0]['questions']['ready']['instructions'] == questions['ready']['instructions'] + '\n' + identity.DEVICE_JUDGE_OWNERSHIP
        assert sent[0]['state'] == {'policy': 'visible_only'}
        assert questions == {'ready': {'type': 'noul', 'instructions': 'Is the editor visible?'}}
    asyncio.run(run())


@pytest.mark.parametrize('local,role', [(True, None), (False, 'mission'), (False, 'draft'), (False, 'reasoner')])
@pytest.mark.parametrize('judge', [False, True])
def test_meta_roles_forward_ownership_without_changing_role(monkeypatch, local, role, judge):
    identity = importlib.import_module('hidagent.identity')
    from hidagent.meta_model import meta_llm
    monkeypatch.setenv('META_API_KEY', 'synthetic-key')
    monkeypatch.delenv('META_CREDENTIALS_FILE', raising=False)
    async def run():
        client = meta_llm(engine='meta_local_hybrid' if local else 'meta_hybrid', role=role)
        prompt = (identity.judge_prompt if judge else identity.device_prompt)('Keep existing role criteria.')
        def handle(request):
            body = json.loads(request.content)
            assert body['messages'][0]['content'] == prompt
            if judge:
                assert identity.DEVICE_OWNERSHIP not in prompt
            return httpx.Response(200, json={'message': {'content': 'ok'}} if local else {'choices': [{'message': {'content': 'ok'}}]})
        await client._http.aclose()
        client._http = httpx.AsyncClient(base_url='https://model.invalid', transport=httpx.MockTransport(handle))
        try:
            assert await client.chat(prompt, 'Test') == 'ok'
        finally:
            await client._http.aclose()
    asyncio.run(run())
