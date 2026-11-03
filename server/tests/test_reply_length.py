import pytest
from hidagent.reply_length import measure_reply
from hidagent.decision import DecisionUnavailable
@pytest.mark.parametrize('text,length,valid',[('a'*280,280,True),('a'*281,281,False),('가'*140,280,True),('가'*141,282,False),('😀'*140,280,True),('😀'*141,282,False),('https://example.com/'+'a'*500,23,True)])
def test_official_weighted_rules_not_python_len(text,length,valid):
 r=measure_reply(text);assert r['weighted_length']==length and r['remaining']==280-length and r['valid'] is valid
 assert r['library']=='twitter-text@3.1.0'
def test_invalid_input_fails_without_guessing():
 with pytest.raises(DecisionUnavailable):measure_reply(None)
