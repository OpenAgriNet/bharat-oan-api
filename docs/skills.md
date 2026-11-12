# Skill prompts and loading

`assets/prompts/manifest.yaml` is the catalogue and configuration entry point.
The `ffs.booking` workflow and its runtime authentication setup are documented in
[ffs.md](ffs.md); v0 exposes five tools through final booking submission with explicit confirmation.
There is no additional capability hierarchy, hand-maintained `sections.json`, or
per-language binding list. Business schemas and implementations stay in
`agents/tools/`; the manifest binds them to workflows, including shared tools.

## Editing an existing workflow

Find the workflow in `assets/prompts/skills/<service>/<workflow>/`. Edit its
`<workflow>_<language>.md` file. YAML front matter contains the short discovery
`summary`; everything below it is the detailed instruction body. Existing Jinja
variables such as `today_date` and the live scheme catalogue remain supported.

For example, the shape of `prices_en.md` is:

```markdown
---
summary: Find mandi prices for a crop, place and date.
---
Detailed workflow instructions, prerequisites and response guidance go here.
```

The summary is shown only while that skill is available but not loaded. The body
is included when preloaded, returned by `load_skill` when discovered mid-turn,
and included in the system prompt on subsequent turns. Tool schemas are taken
from the existing Python registry, never reconstructed from the Markdown.

`assets/prompts/chat/agrinet_<language>.md` contains only conversation-wide rules.
Do not paste tool tables or workflow steps into it. Existing repetitions inside
skills are intentionally preserved for the subsequent prompt review.

## Adding a skill or tool

1. Implement/register any new business tools in `agents/tools/__init__.py`.
2. Add a skill directory and one prompt per language supported by its agent.
3. Add one `skills` entry to the manifest: `prompt_dir`, `tools`, and optional `requires` / `optional_requires`.
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
can ever be loaded. The index and
`load_skill` tool disappear when everything permitted is already loaded.

The model receives the full instructions in the loader result and can call the
new tools on its next step. Successful loads persist through conversation history;
topic changes can add skills. A repeated load is rejected through the existing
agent retry budget, so successful discovery is bounded by the permitted skill set.
There is no automatic eviction in this version. Restart the application after
editing prompt assets or manifest definitions; these are cached for the process.

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
python -m pytest -q tests/test_skills.py tests/test_skill_dependencies.py tests/test_prompt_structure.py \
  tests/test_prompt_language_contract.py tests/test_advisory_prompts.py \
  tests/test_pmkisan_prompt_validation.py tests/test_pmkisan_installment_release.py
```

For the one-off migration comparison, run the same command with
`PROMPT_COMPARE_REF=d8dd247`. Without that variable the historical comparison is
skipped, so later intentional prompt edits are not frozen to an old revision.
The migration comparison reads that baseline from Git. It compares the
instruction-word inventory, including repetitions, in every language. Differences
allowed are replacing the old overview/table headings, renaming the loader, and
the explicitly requested ICAR/document-source policy, shared dependencies,
removal of the contradictory FYM-to-FFS instruction, and the static past-tense
PM-KISAN release announcement. The test accounts for these
changes explicitly and checks all remaining instruction words.
Prompt order and whitespace change as workflows become contiguous; this test does
**not** prove behavioral equivalence. The before/after API benchmark uses the
existing `oan-evaluation/inference/run_simulated_scenario.py` runner. Behavior and
latency must be measured separately; all-preloaded remains the default until then.

## Latest scheme changes

This migration includes `bh-dev` revision `d8dd247`. Its shared scheme-search
instructions live in `skills/schemes/information/shared.md` and are included by
all 13 language files. Edit that file for the shared guidance; localized routing
and discovery summaries remain in their language files. This keeps the upstream
wording without copying the shared text into each translation.

The removed `get_scheme_info` tool is not registered or bound to a skill. The
remaining 50 business tools retain the updated upstream schemas and behavior,
including live-catalog aliases and the configured PM-KISAN announcement. Both
benchmark arms must use this same upstream revision and catalogue snapshot.

## Dependencies: one loading call, shared instructions once

A skill can declare `requires` next to its own tools:

```yaml
skills:
  location.resolve:
    prompt_dir: skills/location/resolve
    tools: [forward_geocode, reverse_geocode]
  mandi.prices:
    prompt_dir: skills/mandi/prices
    requires: [location.resolve]
    tools: [search_commodity, get_mandi_prices]
```

`load_skill("mandi.prices")` loads both skills and their tools in one response.
Dependencies appear before consumers, are loaded transitively and only once,
and persist through history. Already-loaded dependencies are not returned again.
The manifest is the only dependency declaration; language files do not repeat it.
Unknown dependencies, duplicates and cycles fail validation. Loading does not execute any business tool.

Agent/session `enabled_skills` selections name workflow roots; their dependencies
are included automatically within the server's permissions. Preloading a workflow
also preloads its dependencies. An optional environment `allowed_skills` list is a
**hard allowlist**, so it must contain the dependencies as well. Denied tools cannot
be re-enabled by a dependency: unavailable workflows are omitted, or an explicit
inconsistent allowlist fails validation. Selecting a denied workflow fails.

Mandi, weather forecasts, fertilizer recommendations and seed availability reuse
`location.resolve`. Their specific prerequisites remain in each workflow; loading
location does not bypass mandi's date check. `crop.advice` and `schemes.information`
reuse `documents.search`. Document search explains tool usage and grounding;
the calling workflow decides when a document lookup is appropriate.

## Scheme-owned grievance instructions

Grievance workflows live with the scheme that owns them:

- `skills/pm_kisan/grievances/`: PM-Kisan grievance status and the production submission-unavailable message.
- `skills/pmfby/grievances/`: PMFBY registration, verification and ticket status.
- `skills/aif/status/`: AIF loan and grievance status share the same authentication
  workflow; the detailed ticket-format rules live here too.
- `skills/khc/grievances/`: common service selection, other-scheme routing, and
  the reusable KHC registration/status workflow and tools.

The manifest binds PM-Kisan and PMFBY grievance tools only to their own skills.
Those skills and AIF optionally load `khc.grievances` when permitted, so they can
honor an explicit KHC request without loading the other schemes' business tools.
Disabling KHC leaves each scheme's own service available.
KHC instructions are loaded once even when several schemes are active. New
schemes that only use KHC need no copied workflow. All 13 translations retain
their existing wording; this change moves instructions rather than rewriting them.

## Crop sources

Each `advice_<language>.md` includes `flow.md` when `crop.icar` is enabled. The season is never
configured: the farmer's own season is used (see the flow's season question).

`skills/crop/advice/flow.md` contains the common workflow included by all 13
localized crop prompts. After the existing generic/personalised and crop-field
prerequisites, search ICAR–Kisan Sarathi (`search_advisory`) **first**, then check
`search_documents` for the same question, including when ICAR returned an advisory.
Prefer relevant ICAR guidance and use documents for supporting details or when ICAR
has no relevant result. Cite whichever sources actually support the answer. The
pest tool remains additional for crop pests/diseases. Livestock document queries,
scheme-specific routing and IMD requests keep their separate workflows.

This is an explicitly requested behavior change beyond the initial packaging-only
migration. Earlier benchmark results at `bfe7e52` do not validate this version.

## PM-KISAN public installment announcement

`skills/pm_kisan/installments/release.md` holds the configured 24th-installment
announcement. Every localized installment skill includes it directly. The release
has passed, so the announcement uses past tense with no date switch, Python
announcement generator or future-tense fallback. Update the shared Markdown file
when changing the configured announcement. Personal payment/status questions
continue to use the separate PM-Kisan status tools and OTP flow.

## Switching dev-only skills off for production

Set one server-side environment variable (empty/unset preserves the dev default):

```dotenv
DISABLED_SKILLS=ffs.booking,khc.grievances,agristack.profile,crop.icar
```

Remove a name to enable that feature; use `DISABLED_SKILLS=` to enable everything.
Restart/redeploy the API after changing the environment. Unknown skill names fail
validation. The setting is a deny-list: lazy loading, old conversation history and
session selections cannot enable a denied skill.

| Skill | Production baseline | Overlap / effect of disabling |
|---|---|---|
| `ffs.booking` | Disable until released | Removes purchase/booking routing and skips restoring FFS chat context. GFR recommendations remain available. |
| `khc.grievances` | Disable until released | Optional fallback for PM-KISAN, PMFBY and AIF; their own status/grievance services remain. |
| `agristack.profile` | Disable until released | No profile lookup or profile instructions in chat. Browser coordinates and normal place/crop questions remain available. |
| `crop.icar` | Disable until released | Owns `search_advisory`. Removes its workflow and question/order guards; existing crop/pest, document search and NPSS tools remain. |
| `weather.agromet` | Already present in `bh-main`; keep enabled | Can be disabled separately; weather/document helpers then omit IMD advisory offers. |

`crop.advice` is the existing crop/pest workflow. `crop.icar` requires it, but
`crop.advice` does not require ICAR. The ICAR instructions stay in the existing
localized crop files and are rendered only when ICAR is permitted. This keeps
the translations and existing dev flow intact.

KHC is an `optional_requires` dependency: load it alongside a scheme service when
permitted, skip it when disabled. Real `requires` dependencies remain mandatory.
For example, disabling `location.resolve` also removes weather, mandi, GFR and
seed workflows that need location. Disabling `documents.search` removes crop and
scheme-information workflows that need it. Shared `search_terms` remains visible
while at least one enabled skill owns it.

These switches control chat skills and their shared-helper behavior. They do not
remove Python modules, delete saved state or unregister existing HTTP callback
routes. AgriStack consent and normal API authentication still apply. No actual
production environment file is edited by this PR.

Keep discovery descriptions distinct: crop advice owns crop/pest requests;
document search is a supporting lookup with direct livestock coverage. GFR owns
fertilizer-dose recommendations; buying fertilizer explicitly hands off to FFS
only when FFS is enabled. The handoff text is omitted from GFR and discovery when
FFS is disabled. Its enabled wording and booking implementation are unchanged.
ICAR's browser-location restriction is also omitted when ICAR is disabled.

With KHC disabled and all three original scheme grievance skills enabled, the
global clarification rule reuses production's native scheme-selection text before
any identifiers/OTP are collected. Scheme information retains production's
other-scheme redressal fallback without its obsolete filing-availability claim.
Neither rule adds a dependency between the individual scheme services. Status
offers use the enabled services listed by the search result, then require the
farmer's agreement; direct status requests go straight to the matching service.

The switches do not roll back dev's shared behavior improvements: named-crop pest
search validation, public-announcement versus personal-status routing, contextual
moderation, native conversation history, stream deduplication and vLLM language
constraints remain. Tool-name parity alone is not production behavior equivalence.
