# Chat script constraints and Markdown compatibility

Branch `feat/chat-guided-decoding-bh-dev`, based on fetched `bh-dev`
`f08d8bd714a36ec170584f2f4749ef751f1a2260`. Validated 22 October 2026.

## Behavior

For Kannada, Tamil, Malayalam, Telugu, Bengali, Assamese and Gujarati, each
selected vLLM attempt receives `structured_outputs.regex`. The allowlist permits
the target script, printable ASCII/whitespace, and Unicode Common/Inherited
characters. The seven regexes match the original OD-2764 policy exactly.
Other languages and provider kinds receive no override. The current chat runner
uses one streaming execution path, including runs without a UI chunk sink;
the setting is selected anew for each fallback attempt.

ASCII includes whole English sentences, URLs, identifiers and Markdown. This is
script restriction, not a guarantee of linguistic purity or factual accuracy.
No new dependency or deployment configuration is required by the application.
The serving backend must support vLLM's `structured_outputs.regex` contract.

The same branch removes arbitrary overlap removal from ordinary text deltas.
Previously, chunks `₹3,0`, `0`, `0` reached the UI as `₹3,0`. Deltas now append
unchanged after thinking filtering and existing tool-boundary deduplication.
The tool-boundary deduper and final-output normalizer are unchanged.

## Validation

- **130 focused offline tests passed.** Script/format policy, provider gates,
  actual Pydantic HTTP request bodies with mocked transport, tool-result loops,
  visible/final answer equality, repeated-digit/Markdown deltas, routing/fallback,
  tool-boundary deduplication, and existing production contracts.
- **28 sequential live model requests** on isolated Gemma 4 31B, vLLM 0.30.0,
  port 8867. All seven languages copied the comprehensive Markdown fixture exactly.
  All seven tool-call/answer pairs succeeded and retained the tool's price.
  All 21 text responses satisfied their script regex.
- **Adversarial limit:** one bare Assamese-configured request demanding forbidden
  scripts looped in permitted English until the 1,200-token cap. Repeating with
  the current rendered Assamese application prompt terminated normally; the
  unguided control emitted forbidden scripts. This is a small diagnostic, not
  a comprehensive answer-quality evaluation or proof that loops cannot occur.
- **Real streaming capture:** Kannada/Gujarati model outputs exactly matched
  the fixture. Replaying their 268/269 actual content chunks through unchanged
  `bh-dev` corrupted `₹3,000`; replay through the fix preserves every character.
- **14 browser cases passed:** seven languages at desktop/mobile widths using
  OAN-UI's actual `TextBubble` from source `6d3dee74`, React Markdown 10.1.0,
  remark-gfm 4.0.1 and rehype-raw 7.0.0. Checked headings, emphasis, lists, tables,
  links, image markup, task lists, fenced code, details, symbols, emoji and final
  DOM equality after incremental updates. No browser JavaScript errors.

The browser fixture uses local test styling/fonts and an intercepted image;
it does not exercise the complete authenticated frontend or real external tools.
Model probes use deterministic test prompts/tools and temperature zero, not the
full production workflow or its sampling settings. Production was not contacted.

Separate known baseline failure: `test_collapse_adjacent_duplicate_sentence_with_minor_punctuation_variation`
still loses a final period in `_collapse_adjacent_duplicate_text` on untouched
`bh-dev`. That final-answer normalization behavior is outside this streaming fix.

## Reproduce

Run with the project's dependencies and pytest installed; dummy URLs prevent
import-time agent construction from requiring credentials:

```sh
LANGFUSE_TRACING_ENABLED=false \
AZURE_GPT54_MINI_BASE_URL=https://unused.invalid/v1 AZURE_GPT54_MINI_API_KEY=test \
AGRINET_GEMMA_BASE_URL=http://unused.invalid/v1 AGRINET_GEMMA_MODEL_NAME=test \
python -m pytest -q tests/test_guided_decoding.py tests/test_chat_guided_decoding.py \
  tests/test_model_registry_providers.py tests/test_model_routing.py \
  tests/test_stream_dedupe.py tests/test_production_reconciliation.py
```

Saved live requests/responses, reproducible probe/browser scripts, source snapshot,
screenshots and stream replay results are in the local validation workspace:
`/mnt/raid/gautam/adhoc/chat-guided-decoding-20261022/`.
