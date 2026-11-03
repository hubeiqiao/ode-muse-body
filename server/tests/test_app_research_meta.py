"""Offline wire contracts: real client serialization, synthetic frames, no API/device."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from hidagent.app_research import Card, Detail, Returned
from hidagent.app_search import next_search_actions
from hidagent.decision import DecisionUnavailable
from hidagent.meta_model import MetaCloudLLM
from test_app_research import GOAL, ack, agent, detail, result


@pytest.mark.parametrize('schema,response', [(Card, result()), (Detail, detail()), (Returned, {'ready': True})])
def test_research_wire_matches_working_exploration_client(schema, response):
    async def run():
        client = MetaCloudLLM('muse-spark-1.3', 'synthetic-key')
        a = agent([]); a.actor = a.planner = client
        seen = []
        async def handle(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(response)}}]})
        await client._http.aclose()
        client._http = httpx.AsyncClient(base_url='https://api.meta.ai/v1', transport=httpx.MockTransport(handle))
        from hidagent.app_research import _read, binding
        state = dict(steps=a.steps, binding=binding(a), deadline_start=None, frames=[])
        try:
            # Same call format as ExplorationAgent._read_source_once.
            await client.chat('Read public source', 'Topic', a.screen.frame, max_tokens=2400, temperature=0)
            data, _ = await _read(a, state, GOAL, schema, 'Read the visible public screen.')
            assert data == schema.model_validate(response)
            good, research = seen
            assert set(research) == set(good) == {
                'model', 'messages', 'temperature', 'reasoning_effort', 'max_completion_tokens'}
            assert {k: v for k, v in research.items() if k != 'messages'} == {
                k: v for k, v in good.items() if k != 'messages'}
            assert research['messages'][1]['content'][0] == good['messages'][1]['content'][0]
            assert research['messages'][1]['content'][0]['image_url']['url'].startswith('data:image/jpeg;base64,')
            assert not {'response_format', 'json_schema', 'tool_choice', 'max_tokens'} & research.keys()
        finally:
            await client._http.aclose()
    asyncio.run(run())
