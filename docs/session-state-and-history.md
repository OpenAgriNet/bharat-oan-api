# Session state, history budget and skill preloading

Branch `feat/session-state-history` (based on bh-dev `4fa67fd`). This note explains what the chat agent
now sends to the model on every request, where per-session facts are kept, and how to switch back to the
previous behaviour.

## Why

- **bh-dev resent every old tool result.** History was trimmed to a 64k-token estimate, but that estimate
  also counted the stored copy of the ~50k-token system prompt, so only ~14k was left for turns, and every
  kept turn carried its full tool outputs (scheme search, document search and pest search results are
  often 17k-30k characters each). Requests grew with every turn.
- **A plain "last 3 turns" window lost details.** In a 24-turn long-session benchmark it scored lower on
  answer accuracy than bh-dev (3.40 vs 3.66 out of 4, worse in 11 of 12 languages). The judges' reasons
  were details given more than three turns earlier, e.g. a mobile number re-asked in a grievance flow.
- **The prompt itself was too big.** With all 22 skills preloaded the system prompt and tool definitions
  were 51.5k tokens in English and 76.1k in Odia before any history, so Odia and long Punjabi sessions
  overflowed the 100k context.

The fix separates three kinds of information:

1. **Workflow facts** a later turn needs (identifiers, OTP step, internal IDs, reference numbers) are saved
   by the tools themselves in a small per-session store.
2. **The conversation** is sent as question/answer text only, within a token budget.
3. **Instructions** come from a smaller set of preloaded skills; other skills load on demand.

## What the model receives on each request

| Part | Source |
|---|---|
| System prompt + loaded skills | Rendered fresh on every request (`agents/skills.py` `render_prompt`). Preloaded skills plus any skill loaded earlier in the session. |
| Conversation history | Earlier questions and answers. Newest turns within `HISTORY_CONVERSATION_TOKENS`, never fewer than `HISTORY_MIN_TURNS`. |
| Old tool calls and results | Kept only for the last `HISTORY_TOOL_TURNS` turns (0 = none). |
| Session state | One line in the current user message: `**Session state (saved by tools in earlier turns):** {...}` |
| Current turn | The new question, and the agent's own tool calls and results within this turn. |

With the eval setting (`HISTORY_TOOL_TURNS=0`, `HISTORY_CONVERSATION_TOKENS=10000`, `HISTORY_MIN_TURNS=3`),
no old tool result is ever resent; whatever a later turn needs from an earlier tool comes from session state.

The first stored message's system-prompt part is always kept, even when its turn is dropped, because
pydantic-ai replaces that part with the current dynamic system prompt; without it no system prompt is sent.

### History selection (`app/utils.py`)

- `split_turns(history)`: one list per farmer turn (prompt, tool calls/results, answer).
- `last_user_message(history)`: the latest farmer prompt text.
- `keep_tool_results(history, n)`: drops tool calls and their results together (the API rejects a result
  without its call) from all but the last `n` turns; text next to a dropped call stays.
- `recent_turns(history, max_tokens, min_turns)`: newest turns within the budget, at least `min_turns`.
  Only conversation counts, not the stored system prompt.
- `estimate_tokens(text)`: ASCII characters / 4 + other characters / 2. Measured Gemma ratios on chat text
  were English ~3.8, Hindi ~3.4 and Punjabi ~2.2 characters per token, so this does not undercount
  Indic scripts.
- `build_model_history(history, tool_turns, max_tokens, conversation_tokens, min_turns)`: the composer used
  by `app/services/chat.py`. With `tool_turns=None` and `conversation_tokens=None` it is exactly bh-dev's
  `trim_history` + `filter_thinking_from_history`.

## Session-state store (`app/services/session_state.py`)

- **One section per workflow**, e.g. `pmkisan`, `pmfby`, `khc`, `ffs`, `place`, `skills`. A module registers
  its section once with `register_section(name, ttl=..., view=...)`.
- **Redis key**: namespace `session-state:<section>`, key = SHA-256 of the session ID (the session ID itself
  is never in the key).
- **Writing**: a tool calls `remember(ctx, section, **fields)` (or `forget(ctx, section)`). This only changes
  the in-memory state of the current turn; it works in sync and async tools and costs no network call and
  no model call. A field set to `None` is removed.
- **Saving**: at the end of the turn `save_changed_state(deps)` writes each changed section once, in the same
  step that saves the message history (`app/services/chat.py`), so history and state are saved together.
- **Loading**: `load_state(session_id)` reads all registered sections once per turn (`app/routers/chat.py`);
  they are passed to `stream_chat_messages` as `session_state` and kept on `FarmerContext.session_state`.
- **What the model sees**: `render_state` prints all sections as compact JSON. A section's `view` can reduce
  or hide it: FFS shows only its chat context (never the login token), and `imd` and `skills` are hidden.
- **Saved history** never keeps the state line: `clean_chat_history` strips it like the old FFS line, so a
  stale snapshot is not resent from old prompts.
- **TTL**: 24 hours by default (same as message history); FFS 20 minutes (its login lifetime).
- **Personal data**: sections can hold mobile numbers, registration numbers and, briefly, an OTP. Keys are
  hashed, values are never logged, and they expire with the TTL.
- **Size**: a few hundred bytes per used workflow, typically well under 2 KB per session, against ~250 KB of
  stored message history per long session (about 0.1%).

## What each tool stores

Values are written only after the provider accepted the call (OTP sent, OTP verified, record returned).
Failed calls, rejected OTPs and HTTP errors write nothing.

| Section | Written by | Fields |
|---|---|---|
| `pmkisan` | PM-KISAN status and grievance tools | `reg_no` or `phone_number`, `status_otp_step`, `grievance_otp_step` (`sent`/`verified`), `grievance_no` after submit |
| `pmfby` | PMFBY status and grievance tools | `phone_number` (exactly as passed: it keys the transaction ID), `status_otp_step`, `grievance_otp_step`, `grievance_otp` (between verification and submit only), `year`, `season`, `application_no`, `ticket_no` |
| `kcc` | KCC tools | `mobile`, `otp_step`, `application_nos` when several applications are listed |
| `aif` | AIF tools | `beneficiary_id`, `otp_step` (one verification covers loan and grievance status), `loan_application_number` |
| `khc` | KHC tools | `mobile`, `otp_step`, `farmer_type`, the six registration IDs with names (state, village, scheme + division, problem category, sub-category) or a short `problem_options` list (20 or fewer sub-categories), `grievance_number`, `status_otp_step` |
| `shc` | Soil health card status | `phone_number`, `cycle` (only when a card is returned) |
| `smam` | SMAM status | `search_type`, `search_value` (only when an application is found) |
| `gfr` | Fertilizer recommendation tools | `state_id`, `district_id`, `crops` (registry ID -> label, only for listings of 10 or fewer, at most 10 kept), `phone_no`, `cycle` |
| `place` | `forward_geocode`, IMD advisory by place | `name`, `latitude`, `longitude`, `kind` of the last place the farmer named (previously its own Redis key) |
| `imd` | IMD agromet note | `mentioned` (hidden): the advisory is offered once per conversation |
| `ffs` | FFS service | Its whole workflow state, as before; the model sees only `chat_context` |
| `skills` | `load_skill` | `loaded` skill names (hidden): restored each turn so the system prompt includes them |

Stale data is cleared: a new OTP resets the step and drops any stored OTP; a new identifier overwrites the
old one; a new KHC state clears village/scheme/problem IDs; a successful KHC registration clears the
in-progress IDs.

**Not stored, on purpose**: results the answer already tells the farmer (payment status, policy details,
reports), search results (documents, schemes, pests, videos), weather, mandi prices, crop advisories,
image analysis, glossary/commodity lookups, SATHI seed lookups and the Amul/Maha/Bihar networks.
AgriStack keeps its own profile cache.

### Decisions taken

- Identifiers the farmer typed are stored, so a long session does not re-ask them after old turns are trimmed.
- OTP values are stored only until the call that consumes them. In practice only the PMFBY grievance OTP
  qualifies (submit verifies it again); every other flow uses its OTP once.
- All per-session state moves into this one store (named place, IMD flag, FFS, loaded skills).
- The NPSS "waiting for location" marker is not moved: the current NPSS tool never produces it (it falls
  back to a default location), and `find_pending_npss_image_url` only serves sessions saved before that.

## Skill preloading

`assets/prompts/manifest.yaml` now sets `preloaded_skills` for the chat agent. Other enabled skills are
listed in the system prompt with a one-line summary and load with `load_skill` when needed; a loaded skill
stays loaded for the session (saved in the `skills` section).

Chosen from production tool calls over 14 days (about 227k chat traces). PM-KISAN and document search are
always preloaded.

| Skill | Calls in 14 days | Preloaded |
|---|---|---|
| pm_kisan.status | 34,126 | yes |
| schemes.information | 24,809 | yes |
| pm_kisan.grievances | 16,540 | yes |
| crop.advice | 6,191 | yes |
| documents.search | 5,250 | yes (always) |
| mandi.prices | 5,097 | yes |
| location.resolve | 3,860 | yes (required by weather and mandi) |
| pmfby.status | 2,502 | yes |
| weather.forecast | 1,287 | yes |
| pm_kisan.installments | no tools | yes (PM-KISAN always) |
| seeds.availability | 1,051 | on demand |
| kcc.status | 638 | on demand |
| smam.status | 506 | on demand |
| pmfby.grievances | 481 | on demand |
| soil_health.status | 393 | on demand |
| aif.status | 204 | on demand |
| fertilizer.recommendations | 64 | on demand |
| khc.grievances, ffs.booking, agristack.profile, crop.icar, weather.agromet | 0 (not enabled in production) | on demand |

System prompt + visible tool definitions (Gemma tokenizer, raw text, before any history):

| Language | All preloaded | Usage-based preload |
|---|---|---|
| English | 51.5k | 19.7k |
| Hindi | 52.5k | 20.2k |
| Tamil | 53.8k | 21.4k |
| Punjabi | 66.1k | 30.3k |
| Odia | 76.1k | 37.4k |

While `khc.grievances` is not loaded, its status-routing note is still added to the system prompt, as
`khc_service_note` requires (the FFS routing note works the same way).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `HISTORY_TOOL_TURNS` | unset | Turns whose tool calls/results are resent. Unset = all kept turns (bh-dev). `0` = none. |
| `HISTORY_CONVERSATION_TOKENS` | unset | Conversation budget for history. Unset = bh-dev's 64k `trim_history`. |
| `HISTORY_MIN_TURNS` | `3` | Minimum newest turns kept when a budget is set. |

With all three unset, history selection is identical to bh-dev. Skill preloading is set in the manifest;
`preloaded_skills: all` restores the previous behaviour.

## Tests

- `tests/test_session_state.py`: store (merge, remove, change tracking, round trip, hashed keys, views,
  hidden sections), state line in the user message and its removal from saved history, the history composer
  (tool pairs, system-prompt slot, budget with minimum turns, bh-dev equivalence) and loaded-skill restore.
- `tests/test_session_state_pmkisan.py`, `_pmfby.py`, `_khc.py`, `_scheme_status.py` (KCC, AIF, SHC, SMAM,
  GFR): what each tool stores on success, on failure, OTP clearing and stale-field clearing.
- Existing tests updated where they assumed FFS-only context or all-preloaded skills (those now request
  `preloaded_skills='all'` explicitly).
- Full suite: no new failures compared with bh-dev in the same local environment.

## Open items

- **KHC categories**: when a scheme has more than 20 sub-categories, the chosen category/sub-category IDs
  are not saved. The threshold is a guess; check against real KHC catalogue sizes.
- **PMFBY status OTP** is not stored. If the provider accepts the same OTP twice, storing it would allow a
  second status check (another season) without a new OTP.
- **KHC helpers** (`khc_service_note`, `_resolve_concern`) read earlier question/answer text, so they depend
  on those turns surviving the history budget.
- **Token estimate** is a character heuristic, not the tokenizer.
- **Evaluation**: a fourth benchmark arm (`state`: this branch with `HISTORY_TOOL_TURNS=0`,
  `HISTORY_CONVERSATION_TOKENS=10000`, `HISTORY_MIN_TURNS=3`) runs the 24-turn long-session benchmark in all
  13 languages against bh-dev, bh-dev without old tool results, and the 3-turn window. Results pending.
