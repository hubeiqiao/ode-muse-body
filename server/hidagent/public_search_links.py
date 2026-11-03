"""Allowlisted native public-search URIs, not a launcher or publication tool.

The existing goal schema owns app/query validation. No caller-supplied URL,
route, account or action can select a handler. Device execution remains gated
by the browser's actual focus, explicit Open prompt and visible query/results.
"""
import json
from urllib.parse import quote
from .app_search import PREFIX,search_goal

REDNOTE_REFERENCE='https://pages.xiaohongshu.com/activity/deeplink'


def build_public_search_link(goal):
    parsed=search_goal(PREFIX+json.dumps(goal))
    if parsed['app']!='rednote':return None
    return {'app':parsed['app'],'query':parsed['query'],
            'url':'xhsdiscover://search/result?keyword='+quote(parsed['query'],safe=''),
            'reference':REDNOTE_REFERENCE}
