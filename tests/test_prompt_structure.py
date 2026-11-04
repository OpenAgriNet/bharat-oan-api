"""Multilingual inventory and wording-preservation comparison against Git, never saved hashes."""
from collections import Counter
import io
import os
import re
import subprocess
import tarfile

from jinja2 import DictLoader, Environment
import pytest

from agents.skill_catalog import PROMPT_DIR, get_catalog
from agents.skill_policy import SkillState
from agents.skills import render_prompt
from tests.test_skills import CONTEXT, LANGUAGES


def test_inventory_and_language_coverage():
    cat = get_catalog()
    assert len(LANGUAGES) == 13
    expected = {}
    for agent, config in cat.manifest.agents.items():
        names = cat.manifest.skills if config.enabled_skills == 'all' else config.enabled_skills
        for name in names:
            folder = cat.path(cat.manifest.skills[name].prompt_dir)
            stem = folder.name
            variant = config.skill_variants.get(name)
            if variant:
                folder /= variant
            for language in config.languages:
                expected.setdefault(folder, set()).add(f'{stem}_{language}.md')
    found = {p for p in (PROMPT_DIR / 'skills').rglob('*.md') if re.search(r'_[a-z]{2,3}\.md$',p.name)}
    assert found == {folder / filename for folder, files in expected.items() for filename in files}, \
        'Unregistered skill/variant files or missing translations'
    for lang in LANGUAGES:
        root = (PROMPT_DIR / 'chat' / f'agrinet_{lang}.md').read_text()
        assert 'capability_enabled' not in root and '{% include' not in root
        assert not any(tool in root for tool in cat.tools), 'Workflow tool instructions leaked into global prompt'
    assert not (PROMPT_DIR / 'capabilities').exists()
    assert not list(PROMPT_DIR.glob('agrinet_*.md'))


@pytest.fixture(scope='module')
def old_prompts():
    ref = os.environ.get('PROMPT_COMPARE_REF')
    if not ref:
        pytest.skip('Set PROMPT_COMPARE_REF for a one-off migration wording comparison')
    # Full baseline is named in the migration report; allow an explicit comparison revision.
    archive = subprocess.check_output(['git', 'archive', ref, 'assets/prompts'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        sources = {m.name.removeprefix('assets/prompts/'): tar.extractfile(m).read().decode()
                   for m in tar.getmembers() if m.isfile()}
    return Environment(loader=DictLoader(sources), autoescape=False)


def content_tokens(text):
    return Counter(re.findall(r'[^\s|#*]+', text))


@pytest.mark.parametrize('language', LANGUAGES)
def test_every_instruction_word_is_preserved(language, old_prompts):
    before = old_prompts.get_template(f'agrinet_{language}.md').render(**CONTEXT)
    # Only old overview/table headings are replaced by per-skill presentation.
    before = before.replace('load_capability', 'load_skill').replace('load a capability', 'load a skill').replace('capability-loading', 'skill-loading')
    original = before.splitlines()
    headings = [line for line in original if line.startswith('## ')]
    removed = {headings[0], headings[3]}
    table_start = original.index(headings[3])
    table_header = next(line for line in original[table_start+1:] if line.startswith('|'))
    removed.add(table_header)
    removed.add('|---|---|---|---|')
    before = '\n'.join(line for line in original if line not in removed)
    after = render_prompt(SkillState(), language, CONTEXT)
    after = '\n'.join(line for line in after.splitlines()
                      if line not in {'| Query | Tools | Source | Instructions |', '|---|---|---|---|'})
    missing = content_tokens(before) - content_tokens(after)
    added = content_tokens(after) - content_tokens(before)
    assert not missing and not added, {'missing':missing,'added':added}
