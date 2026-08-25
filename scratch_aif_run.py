"""Drive the agrinet agent in-process for AIF testing (avoids the 8-worker OOM on this box).

usage: python scratch_aif_run.py <session_id> "turn 1" "turn 2" ...
"""
import asyncio
import sys

from fastapi import BackgroundTasks

from app.routers.chat_query_utils import get_session_history
from app.services.chat import stream_chat_messages


async def main() -> None:
    session_id = sys.argv[1]
    turns = sys.argv[2:]

    for turn in turns:
        session_id_resolved, history = await get_session_history(session_id)
        print("-" * 64, flush=True)
        print(f">>> FARMER: {turn}", flush=True)
        print("<<< AGENT: ", end="", flush=True)

        background_tasks = BackgroundTasks()
        async for chunk in stream_chat_messages(
            query=turn,
            session_id=session_id_resolved,
            source_lang="en",
            target_lang="en",
            user_id="aif-test",
            history=history,
            background_tasks=background_tasks,
            channel="BharatVistaar",
            is_image_analysis=False,
            latitude=None,
            longitude=None,
            qid=None,
            current_user={"channel": "BharatVistaar", "mobile": None},
        ):
            sys.stdout.write(chunk)
            sys.stdout.flush()
        print(flush=True)
        await background_tasks()

    print("=" * 64, flush=True)
    print(f"LANGFUSE SESSION ID: {session_id}", flush=True)


asyncio.run(main())
