# Plan: Synthetic Conversation Generation Pipeline

## Overview

Build `synthetic/generate.py` — the conversation generation engine that orchestrates multi-turn conversations between the synthetic user agent and agrinet agent, with full metadata capture and parallelization support.

## Architecture

```
generate_random_environment()     → ConversationEnv (date, lang, models, settings)
generate_random_profile(...)      → FarmerProfile   (already exists)
run_conversation(env, profile)    → ConversationRecord (messages, metadata)
generate_batch(n, max_parallel)   → writes JSONL to output dir
```

## File: `synthetic/generate.py`

### 1. `ConversationEnv` — Randomized environment/system config

A Pydantic model holding the randomized "world state" for one conversation:

```python
class ConversationEnv(BaseModel):
    today_date: datetime          # now + random timedelta(0..365 days)
    target_language: str          # "hi" 80%, "en" 10%, "hinglish" 10%
    session_id: str               # uuid4
    user_model: str               # LLM_MODEL_NAME (from env)
    user_model_settings: dict     # serialized AnthropicModelSettings from user_agent
    agrinet_model: str            # LLM_MODEL_NAME (from env)
    agrinet_model_settings: dict  # serialized AnthropicModelSettings from agrinet_agent
```

`generate_random_environment()` creates this:
- `today_date`: `datetime.now() + timedelta(days=random.randint(0, 365))`
- `target_language`: weighted random — `{"hi": 0.80, "en": 0.10, "hinglish": 0.10}`
- `session_id`: `str(uuid4())`
- Model names/settings: read from the agent objects at runtime

### 2. `ConversationRecord` — Output data model

```python
class ConversationRecord(BaseModel):
    session_id: str
    env: ConversationEnv
    profile: FarmerProfile         # full farmer profile (scenario, mood, crops, etc.)
    agrinet_messages_json: str     # agrinet_agent result.all_messages_json() — full trace
    user_messages_json: str        # user_agent accumulated messages — full trace
    turn_count: int
    completed: bool                # True if EndConversation, False if hit max turns
    error: str | None = None       # capture exception text if conversation failed
```

### 3. `run_conversation(env, profile, max_turns=20)` — Core conversation loop

This is the main async function. The loop:

```
1. Build FarmerContext from env + profile (query="", lang_code=env.target_language,
   session_id=env.session_id, today_date=env.today_date)

2. First turn: user agent speaks first (no user_prompt, just system prompt drives it)
   user_result = await user_agent.run(user_prompt="Begin the conversation based on your goal.", deps=profile)

3. Loop (up to max_turns):
   a. Extract user's text from user_result.output (str or EndConversation)
   b. If EndConversation → break
   c. Run moderation on user text:
      mod_result = await moderation_agent.run(user_text)
      farmer_ctx.moderation_str = str(mod_result.output)
   d. Build farmer_ctx with query=user_text
   e. Run agrinet:
      agrinet_result = await agrinet_agent.run(
          user_prompt=farmer_ctx.get_user_message(),
          deps=farmer_ctx,
          message_history=agrinet_history
      )
   f. agrinet_history = agrinet_result.all_messages()
   g. Extract agrinet response text = agrinet_result.output
   h. Run user agent with agrinet response:
      user_result = await user_agent.run(
          user_prompt=agrinet_response,
          deps=profile,
          message_history=user_history
      )
   i. user_history = user_result.all_messages()

4. Build ConversationRecord with all_messages_json() from both agents
```

**Key detail — separate histories**: Each agent maintains its own `message_history` list. The agrinet agent sees user messages as `user_prompt` strings (not raw model messages). The user agent sees agrinet responses as `user_prompt` strings. This is correct because they are separate LLM sessions — one is "the user", the other is "the assistant". They communicate via text, not shared message objects.

### 4. `generate_batch(n, max_parallel, output_dir)` — Parallelized batch runner

```python
async def generate_batch(
    n: int = 100,
    max_parallel: int = 10,
    output_dir: str = "data/synthetic",
    max_turns: int = 20,
) -> Path:
```

- Creates output dir if needed
- Uses `asyncio.Semaphore(max_parallel)` to bound concurrency
- Launches `n` tasks via `asyncio.gather()`, each calling `_run_one_conversation()`
- Each completed conversation is appended to a JSONL file (one JSON object per line)
- File named: `conversations_{timestamp}.jsonl`
- Returns the output file path

`_run_one_conversation()` wraps:
1. `generate_random_environment()`
2. `generate_random_profile(language=env.target_language)`
3. `run_conversation(env, profile)`
4. Catch exceptions → store in `ConversationRecord.error`
5. Return the `ConversationRecord`

The JSONL write uses a lock to avoid interleaving:
```python
async with file_lock:
    with open(output_path, "a") as f:
        f.write(record.model_dump_json() + "\n")
```

### 5. `__main__` block + CLI via argparse

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=10)
    parser.add_argument("--max-parallel", type=int, default=5)
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--output-dir", default="data/synthetic")
    args = parser.parse_args()
    asyncio.run(generate_batch(args.n, args.max_parallel, args.output_dir, args.max_turns))
```

Usage: `python -m synthetic.generate -n 100 --max-parallel 10`

## Files Changed

| # | File | Action |
|---|---|---|
| 1 | `synthetic/generate.py` | **CREATE** — full generation pipeline |

No other files need modification. All existing code (agents, profiles, tools, prompts) is used as-is.

## Output Format (JSONL)

Each line in the output file is a JSON object:

```json
{
  "session_id": "uuid",
  "env": { "today_date": "...", "target_language": "hi", "session_id": "...", "user_model": "...", "user_model_settings": {...}, "agrinet_model": "...", "agrinet_model_settings": {...} },
  "profile": { "name": "Ramesh Kumar", "district": "Indore", "scenario": {...}, ... },
  "agrinet_messages_json": "[{\"kind\":\"request\",...}, {\"kind\":\"response\",...}]",
  "user_messages_json": "[...]",
  "turn_count": 5,
  "completed": true,
  "error": null
}
```

## Key Design Decisions

1. **Semaphore-based parallelism** — `asyncio.Semaphore` is the simplest way to bound concurrent LLM calls. No need for process pools since pydantic-ai is async-native.

2. **JSONL output** — append-friendly, streaming-safe, one conversation per line. Easy to process with `jq`, pandas, or line-by-line readers.

3. **Separate message histories** — the two agents don't share pydantic-ai message objects. They communicate via plain text strings passed as `user_prompt`. This matches real-world behavior where the user and assistant are separate sessions.

4. **Error resilience** — each conversation is wrapped in try/except. Failures are recorded in the output (not lost), and don't block other conversations.

5. **File lock for writes** — `asyncio.Lock` ensures JSONL lines don't interleave when multiple coroutines finish simultaneously.
