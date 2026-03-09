# Telemetry Payload Reference

## Overview

After every chat request completes streaming, `stream_chat_messages` in
`app/services/chat.py` builds a telemetry dict and dispatches it via
`ObservabilityService`:

```python
await observability.log_telemetry(telemetry_data)   # logs locally
await observability.send_telemetry(telemetry_data)  # POSTs to URL
```

The HTTP POST is handled by `app/tasks/telemetry.py → send_telemetry()` which
retries up to **3 times** (1 s wait) on timeout/request errors before giving up.

---

## Destination

| Setting | Default |
|---|---|
| Env var | `TELEMETRY_API_URL` |
| Default URL | `https://dev-vistaar.da.gov.in/observability-service/action/data/v3/telemetry` |
| Method | `POST` |
| Content-Type | `application/json` |

Set `TELEMETRY_API_URL` in `.env` to override.

---

## Payload Schema

```jsonc
{
  "session_id":           "<string>",   // unique conversation session ID
  "user_id":              "<string>",   // caller's user ID (anonymous if not provided)
  "total_input_tokens":   <int>,        // moderation input tokens + agrinet input tokens
  "total_output_tokens":  <int>,        // moderation output tokens + agrinet output tokens
  "moderation_thinking":  "<string>",   // ThinkingPart content from moderation agent (newline-joined)
  "agrinet_thinking":     "<string>",   // ThinkingPart content from agrinet agent (newline-joined)
  "tools_used":           ["<string>"], // flat list of tool names called (excludes 'final_result', 'json')
  "tool_usage": [
    {
      "tool_name": "<string>",          // name of the tool
      "args":      <any>                // arguments passed to the tool (raw from pydantic-ai)
    }, could be more when more tool usage comes
  ]
}
```

---

## Field Details

### `session_id`
- **Source:** `stream_chat_messages(session_id=...)`
- **Type:** `str`
- Identifies the conversation session. Used as a cache key for message history.

### `user_id`
- **Source:** `stream_chat_messages(user_id=...)`
- **Type:** `str`
- Caller-supplied user identifier. Falls back to `"anonymous"` in Langfuse propagation but is passed as-is here.

### `total_input_tokens`
- **Source:** `moderation_run.usage().input_tokens + agrinet_result.usage().input_tokens`
- **Type:** `int`
- Combined input (prompt) tokens consumed across both agent runs.

### `total_output_tokens`
- **Source:** `moderation_run.usage().output_tokens + agrinet_result.usage().output_tokens`
- **Type:** `int`
- Combined output (completion) tokens produced across both agent runs.

### `moderation_thinking`
- **Source:** `ThinkingPart.content` from `moderation_run.all_messages()`
- **Type:** `str` (newline `\n` joined if multiple parts)
- Internal chain-of-thought emitted by the moderation agent. Empty string if the model does not produce thinking.

### `agrinet_thinking`
- **Source:** `ThinkingPart.content` from `new_messages` (agrinet agent, after stream)
- **Type:** `str` (newline `\n` joined if multiple parts)
- Internal chain-of-thought emitted by the agrinet agent. Empty string if the model does not produce thinking.

### `tools_used`
- **Source:** `function_tool_call` events during agrinet streaming
- **Type:** `list[str]`
- Deduplicated-ordered names of tools the agent invoked. Excludes internal pydantic-ai tool names `final_result` and `json`.
- Example: `["get_weather", "get_mandi_prices"]`

### `tool_usage`
- **Source:** Same `function_tool_call` events
- **Type:** `list[dict]`
- Full detail per tool call including arguments. One entry per invocation (not deduplicated).
- Example:
  ```json
  [
    { "tool_name": "get_weather", "args": "{\"location\": \"Delhi\"}" },
    { "tool_name": "get_mandi_prices", "args": "{\"crop\": \"wheat\", \"state\": \"UP\"}" }
  ]
  ```

---

## Code References

| File | Purpose |
|---|---|
| `app/services/chat.py` | Builds `telemetry_data` dict (lines ~221–231) |
| `app/services/observability_service.py` | `ObservabilityService.send_telemetry()` — logs + dispatches |
| `app/tasks/telemetry.py` | `send_telemetry()` — HTTP POST with retry |
| `app/config.py` | `settings.telemetry_api_url` — resolved URL |
| `.env.example` | `TELEMETRY_API_URL` env var |

---

## Example Payload

```json
{
  "session_id": "abc123",
  "user_id": "farmer_user_42",
  "total_input_tokens": 1840,
  "total_output_tokens": 312,
  "moderation_thinking": "",
  "agrinet_thinking": "The user is asking about wheat prices in Uttar Pradesh...",
  "tools_used": ["get_mandi_prices"],
  "tool_usage": [
    {
      "tool_name": "get_mandi_prices",
      "args": "{\"crop\": \"wheat\", \"state\": \"UP\"}"
    }
  ]
}
```
