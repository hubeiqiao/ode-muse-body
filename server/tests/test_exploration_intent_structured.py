from hidagent.exploration_voice import is_exploration_intent


def test_structured_public_search_query_is_not_exploration():
    assert not is_exploration_intent('APP_PUBLIC_SEARCH {"app":"YouTube","query":"ielts space exploration vocabulary"}')


def test_owner_exploration_request_still_detected():
    assert is_exploration_intent('Go explore the iPad and bring back what you discover')
