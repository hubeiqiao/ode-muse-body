from urllib.parse import parse_qs,urlsplit
import pytest
from hidagent.public_search_links import build_public_search_link
from hidagent.decision import DecisionUnavailable


@pytest.mark.parametrize('query',['IELTS daily answers','x&action=post#fragment','/message?account=other','%26target_search=users','quote "test" and +'])
def test_search_text_cannot_change_native_route_or_add_parameters(query):
 link=build_public_search_link({'app':'rednote','query':query});u=urlsplit(link['url'])
 assert u.scheme=='xhsdiscover' and u.netloc=='search' and u.path=='/result' and not u.fragment
 assert parse_qs(u.query)=={'keyword':[query]}
 assert link['query']==query and link['app']=='rednote'


def test_verified_root_trial_uses_the_same_official_uri_shape():
 link=build_public_search_link({'app':'rednote','query':'IELTS daily answers'})
 assert link['url']=='xhsdiscover://search/result?keyword=IELTS%20daily%20answers'
 assert link['reference'].startswith('https://pages.xiaohongshu.com/')
 assert 'published' not in link and 'device_verified' not in link


@pytest.mark.parametrize('goal',[
 {'app':'rednote','query':'IELTS','url':'xhsdiscover://post'},
 {'app':'rednote','query':'IELTS','action':'publish'},
 {'app':'Threads','query':'IELTS'},
 {'app':'xhsdiscover://post','query':'IELTS'},
 {'app':'rednote','query':'hello\nreturn'},
 {'app':'rednote','query':'中文'},
 {'app':'rednote','query':''},
])
def test_untrusted_route_action_or_unsupported_request_is_rejected(goal):
 with pytest.raises(DecisionUnavailable):build_public_search_link(goal)


def test_other_registered_apps_keep_existing_routes_until_independently_verified():
 assert build_public_search_link({'app':'YouTube','query':'IELTS'}) is None
