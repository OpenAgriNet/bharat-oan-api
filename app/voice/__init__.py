"""Voice channel: Sarvam phone calls served by the shared agent tools.

router.py           POST /v1/chat/completions (OpenAI-compatible, what Sarvam calls)
completions.py      OpenAI response/SSE envelope around a turn
service.py          one voice turn: agent run, hold/delay messages, model fallback
history.py          call history (welcome turn, voice-only Redis key and TTL)
languages.py        Sarvam X-Language codes and spoken fixed messages
auth.py             JWT check with the VOICE_AUTH_ENABLED switch
telemetry.py        voice telemetry sender (VOICE_TELEMETRY_* settings only)
completion_telemetry.py  request-count middleware for the dashboard
observability.py    null-safe Langfuse helpers

The agent itself is agents/voice.py; its tools are the shared agents/tools.
"""
