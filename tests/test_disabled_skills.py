"""bh-main comments out FFS, AgriStack and KHC in the manifest: they must be unreachable and unmentioned."""
import re

import pytest

from agents.skill_catalog import get_catalog
from agents.skill_policy import SkillState
from agents.skills import render_prompt

CAT = get_catalog()
OFF = ['ffs.booking', 'agristack.profile', 'khc.grievances']
OFF_TOOLS = {tool for name in OFF for tool in CAT.manifest.skills[name].tools}
# "FFS" alone is also a scheme code in the scheme catalogue, so match the booking flow's wording instead.
MENTIONS = re.compile(r'khc|kisan help|agristack|ffs purchase|through ffs|'
                      + '|'.join(map(re.escape, OFF_TOOLS)), re.I)
ON = sorted(set(CAT.manifest.skills) - set(OFF))
CONTEXT = {'today_date': '12 November 2026', 'crop_season': 'Rabi', 'last_weekday_table': '',
           'vector_scheme_count': 0, 'vector_schemes_block': '', 'khc_service_note': ''}


def test_only_the_disabled_skills_are_off():
    state = SkillState(enabled_skills=ON)
    assert state.permitted == set(CAT.manifest.skills) - set(OFF)
    assert not state.allowed_tools & OFF_TOOLS


@pytest.mark.parametrize('preloaded', [None, []], ids=['preloaded', 'lazy'])
@pytest.mark.parametrize('language', CAT.manifest.agents['chat'].languages)
def test_prompt_never_mentions_disabled_services(language, preloaded):
    state = SkillState(enabled_skills=ON, preloaded_skills=[] if preloaded == [] else ON)
    prompt = render_prompt(state, language, CONTEXT)
    assert not MENTIONS.findall(prompt)
