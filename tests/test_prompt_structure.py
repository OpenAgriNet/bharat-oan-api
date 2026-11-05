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
from agents.skills import render_prompt, skill_instructions
from tests.test_skills import CONTEXT, LANGUAGES


def test_inventory_and_language_coverage():
    cat = get_catalog()
    assert len(LANGUAGES) == 13
    expected = {}
    for agent, config in cat.manifest.agents.items():
        roots = set(cat.manifest.skills) if config.enabled_skills == 'all' else set(config.enabled_skills)
        names = cat.resolve(roots)
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
@pytest.mark.parametrize('release_section', [None, 'Configured release announcement for this date.'])
def test_instructions_preserved_except_requested_source_policy(language, release_section, old_prompts):
    context = dict(CONTEXT)
    if release_section is not None:
        context['pm_kisan_installment_release'] = release_section
    before = old_prompts.get_template(f'agrinet_{language}.md').render(**context)
    # bh-dev appended this note in Python, outside the root template. The skill
    # now includes it directly; the separate test below checks the note wording.
    before += get_catalog().env.get_template('skills/crop/advice/season_note.md').render(
        advisory_default_season='')
    # Only old overview/table headings are replaced by per-skill presentation.
    before = before.replace('load_capability', 'load_skill').replace('load a capability', 'load a skill').replace('capability-loading', 'skill-loading')
    # Explicitly approved changes after the initial byte/word-preserving migration:
    # ICAR first then documents, no FYM->FFS substitution, and shared dependencies.
    cat = get_catalog()
    crop = cat.render_skill('crop.advice', language, context)
    for row in crop.splitlines():
        if row.startswith('|') and '`search_advisory`' in row:
            label = row.split('|')[1].strip()
            prior = [line for line in before.splitlines()
                     if line.startswith('|') and line.split('|')[1].strip() == label]
            assert len(prior) == 1
            before = before.replace(prior[0], row)
    flow = cat.env.get_template('skills/crop/advice/flow.md').render(**context).strip()
    before, count = re.subn(
        r'### Crop advisory flow .*?Do not repeat the per-advisory "Source:" lines that appear inside the tool text\.',
        lambda match: flow, before, flags=re.S)
    assert count == 1
    before = before.replace(
        ' If the farmer asks about F.Y.M. or Farm Yard Manure, call `search_schemes` with an FFS query (e.g. `"FFS Farm Yard Manure"`).', '')
    before = before.replace('`search_documents` / `search_pests_diseases`', '`crop.advice`')
    original = before.splitlines()
    headings = [line for line in original if line.startswith('## ')]
    removed = {headings[0], headings[3]}
    table_start = original.index(headings[3])
    table_header = next(line for line in original[table_start+1:] if line.startswith('|'))
    removed.add(table_header)
    removed.add('|---|---|---|---|')
    before = '\n'.join(line for line in original if line not in removed)
    after = render_prompt(SkillState(), language, context)
    before += '\n' + cat.render_skill('documents.search', language, context)
    state = SkillState(language=language, context=context)
    for name, definition in cat.manifest.skills.items():
        if definition.requires:
            body = cat.render_skill(name, language, context)
            before += '\n' + skill_instructions(name, state).removesuffix(body)
    after = '\n'.join(line for line in after.splitlines()
                      if line not in {'| Query | Tools | Source | Instructions |', '|---|---|---|---|'})
    missing = content_tokens(before) - content_tokens(after)
    added = content_tokens(after) - content_tokens(before)
    assert not missing and not added, {'missing':missing,'added':added}


def test_dynamic_policy_notes_match_git_revision(old_prompts):
    """Execute the real old/new rendering functions without importing network clients."""
    import ast
    root = PROMPT_DIR.parents[1]
    ref = os.environ['PROMPT_COMPARE_REF']
    def function(source, name, namespace):
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), name, 'exec'), namespace)
        return namespace[name]
    for path, name in [('agents/tools/advisory.py', 'advisory_season_note'),
                       ('agents/tools/khc.py', 'khc_service_note')]:
        old = subprocess.check_output(['git','show',f'{ref}:{path}'],text=True)
        new = (root/path).read_text()
        for season in ['', 'Kharif', 'Rabi', 'Zaid']:
            shared = dict(default_season=lambda:season, _turns=lambda messages:messages,
                          _KHC_WORDS=re.compile('KHC'), _STATUS_ID_PROMPT='identifier', _STATUS_OTP_PROMPT='otp')
            before = function(old,name,{**shared,'get_prompt':lambda p,context:old_prompts.get_template(p+'.md').render(**context)})
            if name == 'advisory_season_note':
                after = get_catalog().env.get_template('skills/crop/advice/season_note.md').render(
                    advisory_default_season=season)
                assert before() == after
            else:
                after = function(new,name,{**shared,'get_prompt':lambda p,context:get_catalog().env.get_template(p+'.md').render(**context)})
                for messages,query in [([],''),([], 'KHC status'),([('farmer','KHC')],'AIF')]:
                    assert before(messages,query) == after(messages,query)
