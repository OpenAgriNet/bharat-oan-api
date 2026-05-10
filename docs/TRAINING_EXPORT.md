# Training-Data Export

This module turns existing agent traces into chat-template JSONL that the
Hugging Face TRL trainers (`SFTTrainer`, `DPOTrainer`) accept directly. It is
the upstream half of an offline distillation flow — the runtime app keeps
emitting Langfuse traces as it does today, and an operator periodically pulls
those traces through this adapter to build a training corpus.

## Output schema (v1.0)

Each JSONL line is one self-contained `SFTRecord`:

```json
{
  "schema_version": "1.0",
  "trace_id": "trace-...",
  "messages": [
    {"role": "system",    "content": "..."},
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": "...", "name": "weather", "tool_call_id": "call-1"},
    {"role": "tool",      "content": "...", "name": "weather", "tool_call_id": "call-1"},
    {"role": "assistant", "content": "..."}
  ],
  "metadata": {"environment": "prod", "source_lang": "hi"}
}
```

Roles follow the OpenAI / Hugging Face chat-template convention so a
tokenizer's `apply_chat_template` produces the right training text without
extra pre-processing.

DPO pairs use the same `TrainingMessage` shape:

```json
{
  "schema_version": "1.0",
  "trace_id": "trace-...",
  "prompt":   [{"role": "user", "content": "..."}],
  "chosen":   {"role": "assistant", "content": "..."},
  "rejected": {"role": "assistant", "content": "..."},
  "metadata": {"annotator": "human-1"}
}
```

## Endpoints

- `GET /training/export/sft` — streams `application/x-ndjson` (one
  `SFTRecord` per line). Query params: `since`, `until`, `limit`,
  `include_tool_calls`.
- `GET /training/export/info` — returns the active schema version and
  format catalogue.

The `since` / `until` window is inclusive on both ends; omitting them
exports up to `limit` most-recent traces.

## Wiring to a real trace source

`helpers.training_export.langfuse_trace_to_sft` accepts a plain `dict`
shaped like a Langfuse trace, so the adapter is testable without a live
Langfuse client. The router's `_fetch_traces(...)` is currently a stub
that returns no traces — the follow-up wiring is a single function that
calls `langfuse.api.trace.list(...)` (or any other source) and yields its
items unchanged.

## Offline use (no Langfuse client required)

A small CLI is shipped at `scripts/export_training_data.py` that runs
the same conversion against a local JSON dump of trace dicts. Useful
for ad-hoc exports while the runtime fetch path is being wired:

```bash
# SFT records
python scripts/export_training_data.py \
    --input traces.json \
    --output bharat-oan-sft.jsonl

# DPO pairs (requires a separate preferences file)
python scripts/export_training_data.py \
    --input traces.json \
    --dpo-pairs preferences.json \
    --output bharat-oan-dpo.jsonl
```

`traces.json` is a JSON array of trace dicts (one entry per Langfuse
trace). `preferences.json` is a JSON array of
`{trace_id, chosen, rejected, metadata?}` objects.

## Local verification

```bash
pip install -r requirements.txt
pytest tests/test_training_export.py tests/test_training_export_router.py -v
```

The suite is hermetic — no network, no Langfuse credentials, no Redis.
Router tests load the module by file path so they don't pull the full
runtime import chain.

## Downstream usage example (TRL)

```python
from datasets import load_dataset
from trl import SFTConfig, SFTTrainer
from transformers import AutoTokenizer

ds = load_dataset("json", data_files="bharat-oan-sft.jsonl", split="train")
tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-3B-Instruct")

def fmt(example):
    return tok.apply_chat_template(example["messages"], tokenize=False)

trainer = SFTTrainer(
    model="Qwen/Qwen2.5-3B-Instruct",
    train_dataset=ds,
    formatting_func=fmt,
    args=SFTConfig(output_dir="out", max_seq_length=4096),
)
trainer.train()
```
