"""Focus policy must not demand an 'empty' answer that was never asked."""
from hidagent.vision_judge import JudgeFallback

POLICY={'policy':'app_public_search_focus'}
READY={'type':'noul','instructions':'focused?'}


def test_focus_without_empty_question_approves_confident_ready():
    answers={'ready':{'noul':.995},'blocked':{'noul':0}}
    assert JudgeFallback._approved(POLICY,{'ready':READY},answers)


def test_focus_without_empty_question_still_rejects_unsure_ready():
    answers={'ready':{'noul':.9},'blocked':{'noul':0}}
    assert not JudgeFallback._approved(POLICY,{'ready':READY},answers)


def test_focus_with_empty_question_requires_definite_empty():
    questions={'ready':READY,'empty':{'type':'noul','instructions':'empty?'}}
    assert not JudgeFallback._approved(POLICY,questions,{'ready':{'noul':1},'blocked':{'noul':0},'empty':{'noul':.5}})
    assert JudgeFallback._approved(POLICY,questions,{'ready':{'noul':1},'blocked':{'noul':0},'empty':{'noul':0}})
