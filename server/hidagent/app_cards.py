"""On-demand App Cards, following Mobile Harness's documented file convention.
Only a whitelisted app card is read; card text cannot grant actions or credentials.
"""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def load_app_card(app):
    try:
        index=json.loads((ROOT/'app-skills/index.json').read_text())
        entry=index.get(app)
        if not entry:return None
        path=(ROOT/entry['path']).resolve()
        if not path.is_relative_to(ROOT/'app-skills'):return None
        return {'app':app,'guidance':path.read_text()[:6000],
                'verification':'Guidance only. Recheck actual UI; not proof or authorization.'}
    except (OSError,ValueError,KeyError):return None


def skill_acceptance(name):
    try:
        record=json.loads((ROOT/'app-skills/acceptance.json').read_text()).get(name)
        return record or {'status':'not_yet_verified','scope':'Registered capability, awaiting real device acceptance.'}
    except (OSError,ValueError,TypeError):return {'status':'not_yet_verified'}
