"""
Synthetic conversation generation pipeline.

Orchestrates multi-turn conversations between a synthetic farmer user agent
and the agrinet agent, with environment randomization, parallel batch
generation, and JSONL output.

Usage:
    python -m synthetic.generate -n 10 --max-parallel 5 --max-turns 10 --output-dir data/synthetic
"""

import argparse
import asyncio
import json
import random
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from synthetic.agrinet import agrinet_agent
from synthetic.deps import FarmerContext
from synthetic.models import LLM_MODEL_NAME
from synthetic.moderation import moderation_agent
from synthetic.user import (
    EndConversation,
    FarmerProfile,
    generate_random_profile,
    user_agent,
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

LANGUAGE_WEIGHTS = {"hi": 0.8, "en": 0.1, "hinglish": 0.1}


class ConversationEnv(BaseModel):
    """Randomized environment configuration for a single conversation."""

    today_date: datetime
    target_language: str
    session_id: str
    user_model: str
    user_model_settings: dict
    agrinet_model: str
    agrinet_model_settings: dict


class ConversationRecord(BaseModel):
    """Output record for a single completed conversation."""

    session_id: str
    env: ConversationEnv
    profile: FarmerProfile
    agrinet_messages_json: str
    user_messages_json: str
    turn_count: int
    completed: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Environment generation
# ---------------------------------------------------------------------------


def generate_random_environment() -> ConversationEnv:
    """Build a randomized ConversationEnv."""
    langs, weights = zip(*LANGUAGE_WEIGHTS.items())
    target_language = random.choices(langs, weights=weights, k=1)[0]

    return ConversationEnv(
        today_date=datetime.now() + timedelta(days=random.randint(0, 365)),
        target_language=target_language,
        session_id=str(uuid4()),
        user_model=LLM_MODEL_NAME,
        user_model_settings=dict(user_agent.model_settings)
        if user_agent.model_settings
        else {},
        agrinet_model=LLM_MODEL_NAME,
        agrinet_model_settings=dict(agrinet_agent.model_settings)
        if agrinet_agent.model_settings
        else {},
    )


# ---------------------------------------------------------------------------
# Conversation runner
# ---------------------------------------------------------------------------


async def run_conversation(
    env: ConversationEnv,
    profile: FarmerProfile,
    max_turns: int = 10,
) -> ConversationRecord:
    """Run a multi-turn conversation between the user and agrinet agents."""

    farmer_ctx = FarmerContext(
        query="",
        lang_code=env.target_language,
        session_id=env.session_id,
        today_date=env.today_date,
    )

    # First turn — user agent speaks first
    user_result = await user_agent.run(
        "Begin the conversation based on your goal.",
        deps=profile,
    )

    agrinet_history = []
    user_history = user_result.all_messages()
    turn_count = 0
    completed = False

    for _ in range(max_turns):
        turn_count += 1

        # Extract user text
        user_output = user_result.output
        if isinstance(user_output, EndConversation):
            completed = True
            break

        user_text: str = user_output

        # Moderate the user message
        mod_result = await moderation_agent.run(user_text)

        # Rebuild FarmerContext with the new query + moderation
        farmer_ctx = FarmerContext(
            query=user_text,
            lang_code=env.target_language,
            session_id=env.session_id,
            today_date=env.today_date,
            moderation_str=str(mod_result.output),
        )

        # Run agrinet agent
        agrinet_result = await agrinet_agent.run(
            user_prompt=farmer_ctx.get_user_message(),
            deps=farmer_ctx,
            message_history=agrinet_history,
        )
        agrinet_history = agrinet_result.all_messages()

        # Run user agent with agrinet's response
        user_result = await user_agent.run(
            user_prompt=agrinet_result.output,
            deps=profile,
            message_history=user_history,
        )
        user_history = user_result.all_messages()

    return ConversationRecord(
        session_id=env.session_id,
        env=env,
        profile=profile,
        agrinet_messages_json=agrinet_result.all_messages_json()
        if turn_count > 0
        else "[]",
        user_messages_json=user_result.all_messages_json(),
        turn_count=turn_count,
        completed=completed,
    )


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------


async def generate_batch(
    n: int,
    max_parallel: int = 5,
    output_dir: str = "data/synthetic",
    max_turns: int = 10,
) -> Path:
    """Generate a batch of n synthetic conversations and write to JSONL."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_path / f"conversations_{timestamp}.jsonl"

    semaphore = asyncio.Semaphore(max_parallel)
    write_lock = asyncio.Lock()

    async def _run_one(index: int) -> None:
        async with semaphore:
            env = generate_random_environment()
            profile = generate_random_profile(language=env.target_language)
            scenario_id = profile.scenario.get("id", "unknown")

            try:
                record = await run_conversation(env, profile, max_turns=max_turns)
            except Exception:
                record = ConversationRecord(
                    session_id=env.session_id,
                    env=env,
                    profile=profile,
                    agrinet_messages_json="[]",
                    user_messages_json="[]",
                    turn_count=0,
                    completed=False,
                    error=traceback.format_exc(),
                )

            async with write_lock:
                with open(output_file, "a") as f:
                    f.write(record.model_dump_json() + "\n")

            print(
                f"[{index + 1}/{n}] session={record.session_id} "
                f"scenario={scenario_id} turns={record.turn_count} "
                f"completed={record.completed}"
                + (f" ERROR" if record.error else "")
            )

    tasks = [asyncio.create_task(_run_one(i)) for i in range(n)]
    await asyncio.gather(*tasks)

    print(f"\nWrote {n} conversations to {output_file}")
    return output_file


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic farmer-agent conversations.",
    )
    parser.add_argument(
        "-n",
        type=int,
        default=10,
        help="Number of conversations to generate (default: 10)",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=5,
        help="Maximum concurrent conversations (default: 5)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="Maximum turns per conversation (default: 10)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/synthetic",
        help="Output directory for JSONL files (default: data/synthetic)",
    )
    args = parser.parse_args()

    asyncio.run(
        generate_batch(
            n=args.n,
            max_parallel=args.max_parallel,
            output_dir=args.output_dir,
            max_turns=args.max_turns,
        )
    )


if __name__ == "__main__":
    main()
