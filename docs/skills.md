# Skill prompts and loading

`assets/prompts/manifest.yaml` is the catalogue and configuration entry point.
There is no additional capability hierarchy, hand-maintained `sections.json`, or
per-language binding list. Business schemas and implementations stay in
`agents/tools/`; the manifest binds them to workflows, including shared tools.

## Editing an existing workflow

Find the workflow in `assets/prompts/skills/<service>/<workflow>/`. Edit its
`<workflow>_<language>.md` file. YAML front matter contains the short discovery
`summary`; everything below it is the detailed instruction body. Existing Jinja
variables such as `today_date` and the live scheme catalogue remain supported.

`assets/prompts/chat/agrinet_<language>.md` contains only conversation-wide rules.
Do not paste tool tables or workflow steps into it. Existing repetitions inside
skills are intentionally preserved for the subsequent prompt review.

## Adding a skill or tool

1. Implement/register any new business tools in `agents/tools/__init__.py`.
2. Add a skill directory and one prompt per language supported by its agent.
3. Add one `skills` entry to the manifest: `prompt_dir`, `tools`, and `enabled`.
4. Run the tests below. They detect unregistered tools, orphan skill folders,
   missing languages, duplicate keys, and tool instructions leaking into Agrinet.

A tool can belong to multiple skills. Runtime schemas are deduplicated and existing
per-tool consent/prepare checks still apply. Every bound tool is currently required:
a tool restriction cannot silently expose a half-working workflow.

## Loading settings

```yaml
agents:
  chat:
    prompt_dir: chat
    languages: [en, hi]  # Illustrative; the actual manifest lists all 13.
    enabled_skills: all
    preloaded_skills: all
```

`all` is the migration default. To experiment, replace `preloaded_skills` with a
list of skill IDs; `[]` starts with just the summaries. `enabled_skills` limits what
can ever be loaded. A globally disabled skill is never offered. The index and
`load_skill` tool disappear when everything permitted is already loaded.

The model receives the full instructions in the loader result and can call the
new tools on its next step. Successful loads persist through conversation history;
topic changes can add skills. There is no automatic eviction in this version.

`SkillState(enabled_skills=[...], preloaded_skills=[...])` is the application-level
session selection API. Omitted selections use manifest defaults; `[]` explicitly
means none. The HTTP/UI contract is **not wired yet**. When wiring it, persist the
validated selection with the session rather than accepting arbitrary changes on
every chat message. History cannot widen permissions. Environment variables
`CAPABILITY_*_TOOLS` with non-`all` values fail explicitly instead of being silently
ignored; migrate such experiments to the manifest.

## Optional environments and voice

Dev/prod sections are optional. If configured, the server must set a known
`SKILL_ENVIRONMENT`; the UI cannot pick it. Each environment can narrow
`allowed_skills` and `allowed_tools` (lists or `all`). Explicitly allowing a skill
while denying a required tool is an error. With `allowed_skills: all`, unavailable
workflows are omitted with a diagnostic.

A second agent can reuse the catalogue and select different languages, skill sets,
and preloads. Its `prompt_dir` points to its own Agrinet files. An optional
`skill_variants: {mandi.prices: voice}` selects
`skills/mandi/prices/voice/prices_<language>.md`. This replaces the shared summary
and body; it must cover every language used by that agent. The tool bindings stay
shared. The chat branch does **not** merge or deploy the separate voice backend.

## Validation

```sh
python -m pytest -q tests/test_skills.py tests/test_prompt_structure.py \
  tests/test_prompt_language_contract.py tests/test_advisory_prompts.py \
  tests/test_pmkisan_prompt_validation.py tests/test_pmkisan_installment_release.py
```

The migration comparison reads baseline `e272042` from Git. It compares the
instruction-word inventory, including repetitions, in every language. Differences
allowed are replacing the old overview/table headings and renaming the loader.
Prompt order and whitespace change as workflows become contiguous; this test does
**not** prove behavioral equivalence. The before/after API benchmark uses the
existing `oan-evaluation/inference/run_simulated_scenario.py` runner. Behavior and
latency must be measured separately; all-preloaded remains the default until then.
