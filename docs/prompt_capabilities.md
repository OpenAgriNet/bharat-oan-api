# Prompt capabilities

Default behavior is unchanged: every permitted business tool is loaded initially,
no loader is exposed, and every existing language prompt renders exactly as before.
Existing tool-specific restrictions (including AgriStack consent) still apply.
All 48 tools and 13 language prompts in the current chat `bh-dev` are covered.

## Configuration

Set these in the application's environment or `.env`:

```dotenv
CAPABILITY_ALLOWED_TOOLS=all
CAPABILITY_PRELOADED_TOOLS=all
```

Each accepts `all`, `none`, or comma-separated **exact tool names**. The allowed
list is the hard ceiling, including tools that might be loaded later. Preloaded
tools must be a subset of it; `all` for preload means all allowed tools. Unknown
names or an invalid subset raise an error, rather than silently exposing tools.

For example, permit crop retrieval and PM-KISAN but initially expose crop retrieval:

```dotenv
CAPABILITY_ALLOWED_TOOLS=search_terms,search_documents,initiate_pm_kisan_status_check,check_pm_kisan_status_with_otp
CAPABILITY_PRELOADED_TOOLS=search_terms,search_documents
```

Tools in `allowed - preloaded` can be loaded later via `load_capability`. Related
tools are grouped in `agents/capability_manifest.py`; loading a group enables only
its **allowed** members. It returns the original workflow instructions and never
executes a business action. Location helpers are separately configurable: include
them in the allowed/preloaded lists when the workflow needs geocoding.

Loaded groups stay loaded for the session (no automatic topic-change eviction).
Successful loader results restore state on subsequent turns; service callers
restore from full history **before trimming**. A tighter policy on a later turn
always takes precedence over previously loaded names. Session state is not global.

The future request-level integration point is `FarmerContext.capabilities.policy`
(`ToolPolicy(allowed_tools=..., preloaded_tools=...)`). There are deliberately no
new public API fields yet. Policy controls these agents' business tools, not
moderation, auxiliary agents, authentication, or external service authorization.

## Prompt preservation

Workflow blocks were moved verbatim into `assets/prompts/capabilities/` and included
in their original positions. Shared routing, safety, language, and cross-workflow
rules remain in the main prompt. Some mixed-workflow blocks remain shared to avoid
losing prerequisites; selective mode is not intended to remove every reference to
an unloaded tool. A short availability rule overrides general service descriptions.
Tool descriptions, schemas, model settings, and business implementations are unchanged.

`sections.json` maps fragments to tools. If any mapped tool is loaded, the full
original block is included. A loader also returns the matching blocks, which is
necessary for agents whose system prompt is not reevaluated during a tool loop.
`original_sha256.json` records pre-migration source hashes; tests reconstruct and
compare the original template and rendered text for **every language**. Intentional
future prompt edits must deliberately update that preservation baseline.

Run offline checks from the repository root, with its pinned Pydantic AI installed:

```bash
python -m pytest tests/test_capabilities.py -q
```

These check prompt equality, all-tool coverage, policy validation, session/topic
switches, original tool ordering, and an actual Pydantic AI load→execute loop using
a deterministic local model. They do not establish selective-mode model accuracy
or latency; those require a separate inference benchmark.
