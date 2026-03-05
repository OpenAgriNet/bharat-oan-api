"""FastAPI backend for the Synthetic Conversation Viewer."""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"

app = FastAPI(title="Synthetic Conversation Viewer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _validate_filename(filename: str) -> Path:
    """Validate and return the full path for a JSONL filename."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = DATA_DIR / filename
    if not filepath.exists() or filepath.suffix != ".jsonl":
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return filepath


def _read_records(filename: str) -> list[dict]:
    """Read all JSON records from a JSONL file."""
    filepath = _validate_filename(filename)
    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


@app.get("/api/files")
async def list_files():
    """List JSONL files in the data directory, sorted newest-first."""
    if not DATA_DIR.exists():
        return []
    files = []
    for p in DATA_DIR.glob("*.jsonl"):
        stat = p.stat()
        files.append({
            "name": p.name,
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return files


def _summarize_record(r: dict, filename: str) -> dict:
    """Build a conversation summary from a record."""
    profile = r.get("profile", {})
    env = r.get("env", {})
    scenario = profile.get("scenario", {})
    return {
        "session_id": r["session_id"],
        "file": filename,
        "name": profile.get("name", "Unknown"),
        "scenario_id": scenario.get("id", ""),
        "scenario_category": scenario.get("category", ""),
        "location": f"{profile.get('district', '')}, {profile.get('state', '')}",
        "language": profile.get("language", ""),
        "target_language": env.get("target_language", ""),
        "turn_count": r.get("turn_count", 0),
        "completed": r.get("completed", False),
        "has_error": r.get("error") is not None and r.get("error") != "",
        "mood": profile.get("mood", ""),
        "verbosity": profile.get("verbosity", ""),
    }


@app.get("/api/conversations")
async def list_conversations(file: str = Query(None, description="JSONL filename (optional — omit to list all)")):
    """List conversation summaries. If file is given, reads that file; otherwise lists all files."""
    if file:
        records = _read_records(file)
        return [_summarize_record(r, file) for r in records]

    # List all conversations across all files
    if not DATA_DIR.exists():
        return []
    summaries = []
    for p in sorted(DATA_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        r = json.loads(line)
                        summaries.append(_summarize_record(r, p.name))
        except Exception:
            continue
    return summaries


@app.get("/api/conversation/{session_id}")
async def get_conversation(
    session_id: str,
    file: str = Query(None, description="JSONL filename (optional)"),
):
    """Get a full conversation record by session ID."""
    if file:
        records = _read_records(file)
        for r in records:
            if r["session_id"] == session_id:
                return r
        raise HTTPException(status_code=404, detail=f"Conversation {session_id} not found")

    # Try {session_id}.jsonl first, then search all files
    direct = DATA_DIR / f"{session_id}.jsonl"
    if direct.exists():
        with open(direct) as f:
            for line in f:
                line = line.strip()
                if line:
                    return json.loads(line)

    # Fallback: search all files (for archived multi-record files)
    if DATA_DIR.exists():
        for p in DATA_DIR.glob("*.jsonl"):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        r = json.loads(line)
                        if r.get("session_id") == session_id:
                            return r
    raise HTTPException(status_code=404, detail=f"Conversation {session_id} not found")


@app.delete("/api/conversation/{session_id}")
async def delete_conversation(
    session_id: str,
    file: str = Query(None, description="JSONL filename (optional)"),
):
    """Delete a conversation by session ID."""
    # Try direct file first
    direct = DATA_DIR / f"{session_id}.jsonl"
    if direct.exists():
        direct.unlink()
        return {"deleted": session_id}

    # Fallback: remove from multi-record file
    if file:
        filepath = _validate_filename(file)
        records = _read_records(file)
        filtered = [r for r in records if r["session_id"] != session_id]
        if len(filtered) == len(records):
            raise HTTPException(status_code=404, detail=f"Conversation {session_id} not found")
        if filtered:
            with open(filepath, "w") as f:
                for r in filtered:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        else:
            filepath.unlink()
        return {"deleted": session_id}

    raise HTTPException(status_code=404, detail=f"Conversation {session_id} not found")


# ---------------------------------------------------------------------------
# SSE Simulation endpoint
# ---------------------------------------------------------------------------


class SimulateRequest(BaseModel):
    max_turns: int = 25
    language: Optional[str] = None
    target_language: Optional[str] = None
    scenario_id: Optional[str] = None
    custom_scenario: Optional[str] = None
    force_language_switch: Optional[bool] = None  # Force a mid-conversation language switch


def _sse_event(event: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/api/simulate")
async def simulate(req: SimulateRequest, request: Request):
    """Run a new conversation in real-time, streaming turns via SSE."""

    # Lazy imports to avoid loading heavy ML deps for static browsing
    from synthetic.generate import (
        ConversationEnv,
        ConversationRecord,
        LanguageSwitch,
        generate_random_environment,
        _pick_language_switch,
    )
    from synthetic.user import (
        EndConversation,
        FarmerProfile,
        generate_random_profile,
        user_agent,
    )
    from synthetic.agrinet import agrinet_agent
    from synthetic.deps import FarmerContext, build_moderation_input
    from synthetic.moderation import moderation_agent

    async def event_stream():
        try:
            import random
            from synthetic.mock_data import SAME_LANGUAGE_PROBABILITY, TARGET_LANGUAGE_WEIGHTS

            # Generate environment first (picks target_language)
            env = generate_random_environment(target_language=req.target_language)

            # User language: 90% of the time same as target, 10% random
            user_language = req.language
            if user_language is None:
                if random.random() < SAME_LANGUAGE_PROBABILITY:
                    user_language = env.target_language
                # else: None → profile generator picks randomly from LANGUAGE_WEIGHTS

            profile = generate_random_profile(
                language=user_language,
                scenario_id=req.scenario_id,
            )

            yield _sse_event("env", env.model_dump())

            # Override scenario with custom text if provided
            if req.custom_scenario:
                profile = profile.model_copy(update={
                    "scenario": {
                        "id": "custom",
                        "category": "custom",
                        "description": req.custom_scenario,
                    }
                })

            yield _sse_event("profile", profile.model_dump())

            # Language switch setup
            current_target_lang = env.target_language
            language_switches: list[LanguageSwitch] = []
            if req.force_language_switch:
                # Force a switch: pick turn 2-3 and a different language
                candidates = {k: v for k, v in TARGET_LANGUAGE_WEIGHTS.items() if k != current_target_lang}
                langs, weights = zip(*candidates.items())
                new_lang = random.choices(langs, weights=weights, k=1)[0]
                planned_switch = (random.randint(2, 3), new_lang)
            else:
                planned_switch = _pick_language_switch(current_target_lang, req.max_turns)

            # Run conversation turn-by-turn
            farmer_ctx = FarmerContext(
                query="",
                lang_code=current_target_lang,
                session_id=env.session_id,
                today_date=env.today_date,
                farmer_name=profile.name,
                farmer_phone=profile.phone,
                farmer_aadhaar=profile.aadhaar,
                farmer_state=profile.state,
                farmer_district=profile.district,
                farmer_village=profile.village,
                farmer_crops=profile.crops,
                farmer_land_acres=profile.land_acres,
            )

            # First turn — user speaks first
            user_result = await user_agent.run(
                "Begin the conversation based on your goal.",
                deps=profile,
            )
            user_history = user_result.all_messages()
            agrinet_history = []
            turn_count = 0
            completed = False
            agrinet_result = None

            for _ in range(req.max_turns):
                # Check if client disconnected
                if await request.is_disconnected():
                    return

                turn_count += 1

                # Check for language switch at this turn
                if planned_switch and turn_count == planned_switch[0]:
                    old_lang = current_target_lang
                    current_target_lang = planned_switch[1]
                    language_switches.append(LanguageSwitch(
                        turn=turn_count, from_lang=old_lang, to_lang=current_target_lang,
                    ))
                    yield _sse_event("language_switch", {
                        "turn_number": turn_count,
                        "from_lang": old_lang,
                        "to_lang": current_target_lang,
                    })

                user_output = user_result.output
                is_end = isinstance(user_output, EndConversation)

                if is_end:
                    yield _sse_event("user_message", {
                        "turn_number": turn_count,
                        "text": "[End conversation]",
                        "is_end": True,
                    })
                    completed = True
                    break

                user_text = user_output

                # Send user message immediately
                yield _sse_event("user_message", {
                    "turn_number": turn_count,
                    "text": user_text,
                    "is_end": False,
                })

                # Moderate (with last 3 QA pairs for context)
                mod_input = build_moderation_input(user_text, agrinet_history, limit=3)
                mod_result = await moderation_agent.run(mod_input)

                farmer_ctx = FarmerContext(
                    query=user_text,
                    lang_code=current_target_lang,
                    session_id=env.session_id,
                    today_date=env.today_date,
                    moderation_str=str(mod_result.output),
                    farmer_name=profile.name,
                    farmer_phone=profile.phone,
                    farmer_aadhaar=profile.aadhaar,
                    farmer_state=profile.state,
                    farmer_district=profile.district,
                    farmer_village=profile.village,
                    farmer_crops=profile.crops,
                    farmer_land_acres=profile.land_acres,
                )

                # Run agrinet
                agrinet_result = await agrinet_agent.run(
                    user_prompt=farmer_ctx.get_user_message(),
                    deps=farmer_ctx,
                    message_history=agrinet_history,
                )
                agrinet_history = agrinet_result.all_messages()

                # Extract tool calls from this turn's messages
                tool_calls = []
                for msg in agrinet_result.new_messages():
                    for part in msg.parts:
                        pk = part.part_kind
                        if pk == "tool-call":
                            tool_calls.append({
                                "tool_name": part.tool_name,
                                "args": part.args if isinstance(part.args, str) else json.dumps(part.args),
                            })

                # Send agent response
                yield _sse_event("agent_message", {
                    "turn_number": turn_count,
                    "text": agrinet_result.output,
                    "tool_calls": tool_calls,
                })

                # Run user agent with agrinet's response
                user_result = await user_agent.run(
                    user_prompt=agrinet_result.output,
                    deps=profile,
                    message_history=user_history,
                )
                user_history = user_result.all_messages()

            # Build final record
            record = ConversationRecord(
                session_id=env.session_id,
                env=env,
                profile=profile,
                agrinet_messages_json=agrinet_result.all_messages_json()
                if agrinet_result
                else "[]",
                user_messages_json=user_result.all_messages_json(),
                turn_count=turn_count,
                completed=completed,
                language_switches=language_switches or None,
            )

            # Write one file per conversation
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            output_file = DATA_DIR / f"{env.session_id}.jsonl"
            with open(output_file, "w") as f:
                f.write(record.model_dump_json() + "\n")

            yield _sse_event("done", {
                "record": json.loads(record.model_dump_json()),
                "file": output_file.name,
            })

        except Exception:
            yield _sse_event("error", {"message": traceback.format_exc()})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
