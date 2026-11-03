import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from hidagent.curiosity_plan import choose_topic,HumanPreference
from hidagent.exploration_round import RoundRejected

PROPOSAL={'topic':'Public art','query':'public art','reason':'Explore something people can notice in everyday life.'}

def test_proposal_stays_unverified_and_preserves_explicit_human_feedback():
    async def run():
        writer=SimpleNamespace(model='test-haiku',chat=AsyncMock(return_value=json.dumps(PROPOSAL)))
        result=await choose_topic(writer,'Find something interesting',[HumanPreference('Avoid space topics','human-2','spoken')])
        assert result['kind']=='unverified_exploration_proposal' and not result['evidence']
        assert result['public_search_url']=='https://en.wikipedia.org/wiki/Special:Search?search=public+art'
        assert result['preference_turn_ids']==['human-2']
        sent=json.loads(writer.chat.call_args.args[1]);assert sent['explicit_feedback'][0]['quote']=='Avoid space topics'
    asyncio.run(run())

@pytest.mark.parametrize('bad',[{'query':'https://evil.test/'},{'query':'x\npost now'},{'query':'小红书'}, {'query':'a'}, {'claim':'I found this already'},{'topic':'x'*101}])
def test_model_cannot_supply_arbitrary_urls_commands_or_discovery_claims(bad):
    writer=SimpleNamespace(model='test',chat=AsyncMock(return_value=json.dumps({**PROPOSAL,**bad})))
    with pytest.raises(RoundRejected):asyncio.run(choose_topic(writer,'Explore'))

def test_model_generated_preference_record_is_not_accepted_as_human_feedback():
    writer=SimpleNamespace(model='test',chat=AsyncMock())
    with pytest.raises(RoundRejected):asyncio.run(choose_topic(writer,'Explore',[{'quote':'likes science','source':'model'}]))
    writer.chat.assert_not_called()

def test_preferences_need_original_human_turn_and_supported_modality():
    with pytest.raises(RoundRejected):HumanPreference('likes art','','spoken')
    with pytest.raises(RoundRejected):HumanPreference('likes art','turn-1','inferred')

@pytest.mark.parametrize('history', [
    {'prior_topics': [{'topic': 'Animal regeneration', 'query': 'Axolotl'}]},
    {'prior_discoveries': [{'source_title': 'Axolotl', 'url': 'https://en.wikipedia.org', 'claim': 'Fully aquatic.'}]},
    {'prior_discoveries': [{'url': 'https://en.wikipedia.org/wiki/Axolotl'}]},
])
def test_recent_topic_or_saved_article_is_excluded_and_retried(history):
    async def run():
        repeat = {'topic': 'Animal regeneration', 'query': 'Axolotl', 'reason': 'Interesting.'}
        fresh = {'topic': 'Food chemistry', 'query': 'Sourdough', 'reason': 'A different direction.'}
        writer = SimpleNamespace(model='test', chat=AsyncMock(side_effect=[json.dumps(repeat), json.dumps(fresh)]))
        result = await choose_topic(writer, 'Surprise me', short_article=True, **history)
        assert result['query'] == 'Sourdough'
        assert writer.chat.await_count == 2
        request = json.loads(writer.chat.call_args.args[1])
        assert 'axolotl' in request['excluded_topics_and_queries']
        assert request['exploration_directions']
    asyncio.run(run())


def test_repeated_excluded_proposals_fail_without_authorizing_device_input():
    writer = SimpleNamespace(model='test', chat=AsyncMock(return_value=json.dumps(PROPOSAL)))
    with pytest.raises(RoundRejected, match='recent'):
        asyncio.run(choose_topic(writer, 'Explore', prior_topics=[PROPOSAL]))
    assert writer.chat.await_count == 3


def test_exclusions_use_last_twenty_and_normalize_titles_and_queries():
    async def run():
        history = [{'query': 'Axolotl'}] + [{'query': 'Other'+str(i)} for i in range(20)]
        proposal = {'topic': 'Animal regeneration', 'query': 'Axolotl', 'reason': 'Interesting.'}
        writer = SimpleNamespace(model='test', chat=AsyncMock(return_value=json.dumps(proposal)))
        assert (await choose_topic(writer, 'Explore', short_article=True, prior_topics=history))['query'] == 'Axolotl'
        request = json.loads(writer.chat.call_args.args[1])
        assert len(request['excluded_topics_and_queries']) == 20
        assert 'axolotl' not in request['excluded_topics_and_queries']
        writer.chat.reset_mock()
        writer.chat.side_effect = [json.dumps(PROPOSAL), json.dumps(proposal)]
        result = await choose_topic(writer, 'Explore', prior_topics=[{'query': 'PUBLIC_ART'}])
        assert result['query'] == 'Axolotl' and writer.chat.await_count == 2
    asyncio.run(run())
