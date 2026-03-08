# Scripts

## End-to-end workflow: generate → clean → prepare

```bash
# 1. Generate synthetic conversations
python -m synthetic.generate -n 100 --max-parallel 5 --max-turns 10 --output-dir data/synthetic

# 2. Scan for hallucinated thinking traces (read-only report)
python scripts/scan_hallucinated_thinking.py --data-dir data/synthetic

# 3. Clean up hallucinated thinking traces (in-place)
python scripts/clean_hallucinated_thinking.py --data-dir data/synthetic

# 4. Prepare HF dataset from cleaned conversations
python -m synthetic.prepare_dataset --data-dir data/synthetic --output-dir data/hf_dataset
```

## Script reference

### `scan_hallucinated_thinking.py`

Read-only scan that detects hallucinated thinking traces and prints a report.

Hallucinated thinking traces are a known issue with the OpenAI provider where
the model's thinking block contains regurgitated search results, system prompt
fragments, or Indic-language response text instead of actual reasoning. They are
reliably identified by the pattern: single-part `thinking` message immediately
followed by a single-part `retry-prompt` message.

```bash
# Basic scan
python scripts/scan_hallucinated_thinking.py --data-dir data/synthetic

# Verbose — show every individual instance
python scripts/scan_hallucinated_thinking.py --data-dir data/synthetic --verbose
```

Categories reported:

| Category | Description |
|---|---|
| `search_result_with_codeblock` | Search result content with ``` markers |
| `english_doc_no_reasoning` | English document/advisory text, no reasoning |
| `indic_response_in_thinking` | Actual response (Indic script) written in thinking block |
| `regurgitated_doc_with_translations` | ICAR advisory text with inline `[Hindi]` bracket translations |
| `short_indic_fragment` | Garbled Indic script fragments (< 20 chars) |
| `short_fragment` | Tiny English fragments, numbers |
| `empty` | Empty thinking block |

### `clean_hallucinated_thinking.py`

Removes hallucinated thinking + retry-prompt message pairs in-place. Handles
chains of consecutive failures by iterating until stable.

```bash
# Dry run — see what would change without modifying files
python scripts/clean_hallucinated_thinking.py --data-dir data/synthetic --dry-run

# Apply cleanup in-place
python scripts/clean_hallucinated_thinking.py --data-dir data/synthetic
```

Safe to run multiple times — idempotent on already-clean data.

### `translate_assets.py`

Translates `commodity_codes.json` and `glossary_terms.json` to Indic languages
using the Claude API. Requires `ANTHROPIC_API_KEY`.

```bash
# Migrate commodity_codes.json flat terms to per-language dict (no API needed)
python scripts/translate_assets.py migrate-commodity

# Translate glossary terms to all new languages
python scripts/translate_assets.py translate-glossary

# Translate commodity codes to all new languages
python scripts/translate_assets.py translate-commodity
```
