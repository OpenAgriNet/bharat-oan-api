import asyncio
import time
from unittest.mock import MagicMock

import pytest

from app.services import chat


@pytest.mark.parametrize("behavior", ["hang", "raise"])
def test_flush_never_blocks_or_raises(monkeypatch, behavior):
    def flush():
        if behavior == "hang":
            time.sleep(0.5)
        else:
            raise RuntimeError("langfuse down")

    monkeypatch.setattr(chat, "get_client", lambda: MagicMock(flush=flush))
    monkeypatch.setattr(chat, "LANGFUSE_FLUSH_TIMEOUT_S", 0.1)

    async def run():
        start = time.monotonic()
        await chat._flush_langfuse()
        return time.monotonic() - start

    assert asyncio.run(run()) < 0.4
