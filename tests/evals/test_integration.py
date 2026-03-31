"""
tests/evals/Integration_testing.py
-----------------------------------
Integration tests for bharat-oan-api using DeepEval.
Test data loaded from: tests/evals/dataset/oan_eval_dataset.json

Metric strategy
---------------
- is_decline=false  →  AnswerRelevancyMetric + AG_DAG (requires source citation)
- is_decline=true   →  DECLINE_DAG only
  (separate metric that only checks polite refusal — no citation required)

Run
---
    pytest tests/evals/test_integration.py -v --tb=short -s

Required env vars
-----------------
    OPENAI_API_KEY     - used by DeepEval judge
    OAN_API_BASE_URL   - default: http://localhost:8000
"""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytest
from deepeval.metrics import AnswerRelevancyMetric, GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from dotenv import load_dotenv

from app.tasks.oan_eval_client import OANEvalClient

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL: str = os.getenv("OAN_API_BASE_URL", "http://localhost:8000")
JUDGE_MODEL: str = os.getenv("DEEPEVAL_MODEL", "gpt-4o-mini")
MAX_WORKERS: int = 3
DAG_THRESHOLD: float = 0.7
RELEVANCY_THRESHOLD: float = 0.6
METRIC_CALL_DELAY: float = 2.0

DATASET_PATH: Path = Path(__file__).parent / "dataset" / "oan_eval_dataset.json"


# ---------------------------------------------------------------------------
# Test-case schema
# ---------------------------------------------------------------------------


@dataclass
class OANTestCase:
    name: str
    input: str
    expected_output: str
    context: list[str]
    is_decline: bool = False
    source_lang: str = "en"
    target_lang: str = "en"
    user_id: str = "eval-user"


# ---------------------------------------------------------------------------
# Load test cases from JSON
# ---------------------------------------------------------------------------


def load_test_cases() -> list[OANTestCase]:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found at {DATASET_PATH}. "
            "Create tests/evals/dataset/oan_eval_dataset.json before running."
        )

    raw: list[dict] = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    cases = [
        OANTestCase(
            name=item["name"],
            input=item["input"],
            expected_output=item["expected_output"],
            context=item.get("context", []),
            is_decline=item.get("is_decline", False),
            source_lang=item.get("source_lang", "en"),
            target_lang=item.get("target_lang", "en"),
        )
        for item in raw
    ]

    print(f"[Dataset] Loaded {len(cases)} test cases from {DATASET_PATH}")
    return cases


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_oan_client: Optional[OANEvalClient] = None
_relevancy_metric: Optional[AnswerRelevancyMetric] = None
_ag_dag_metric: Optional[GEval] = None
_decline_dag_metric: Optional[GEval] = None


def get_client() -> OANEvalClient:
    global _oan_client
    if _oan_client is None:
        _oan_client = OANEvalClient(base_url=BASE_URL)
    return _oan_client


def get_relevancy_metric() -> AnswerRelevancyMetric:
    global _relevancy_metric
    if _relevancy_metric is None:
        _relevancy_metric = AnswerRelevancyMetric(
            threshold=RELEVANCY_THRESHOLD,
            model=JUDGE_MODEL,
            include_reason=True,
            async_mode=False,
            verbose_mode=True,
        )
    return _relevancy_metric


def get_ag_dag_metric() -> GEval:
    """DAG metric for valid agricultural responses — requires source citation."""
    global _ag_dag_metric
    if _ag_dag_metric is None:
        _ag_dag_metric = GEval(
            name="AG_DAG",
            criteria=(
                "Evaluate the actual output on three axes:\n"
                "1. DIRECTNESS (0-1): Response starts by addressing the question immediately "
                "with no preamble like 'Let me explain' or 'I will help you'.\n"
                "2. SCOPE ADHERENCE (0-1): Agricultural queries get agricultural content.\n"
                "3. SOURCE CITATION (0-1): Factual agricultural responses include a bold "
                "source citation e.g. **Source: ...** or **स्रोत: ...**.\n\n"
                "Final score = Directness×0.25 + Scope Adherence×0.50 + Source Citation×0.25."
            ),
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.EXPECTED_OUTPUT,
            ],
            threshold=DAG_THRESHOLD,
            model=JUDGE_MODEL,
            async_mode=False,
            verbose_mode=True,
        )
    return _ag_dag_metric


def get_decline_dag_metric() -> GEval:
    """DAG metric for decline responses — only checks polite refusal, NO citation."""
    global _decline_dag_metric
    if _decline_dag_metric is None:
        _decline_dag_metric = GEval(
            name="DECLINE_DAG",
            criteria=(
                "The input is an invalid or out-of-scope query. "
                "Evaluate the actual output on two axes:\n"
                "1. SCOPE ADHERENCE (0-1): The response correctly refuses to answer "
                "the out-of-scope query without providing the requested content.\n"
                "2. POLITENESS (0-1): The response politely redirects the user toward "
                "agricultural/farming topics without being rude or dismissive.\n\n"
                "Final score = Scope Adherence×0.70 + Politeness×0.30.\n"
                "Source citations are NOT required and must NOT affect the score."
            ),
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
            ],
            threshold=DAG_THRESHOLD,
            model=JUDGE_MODEL,
            async_mode=False,
            verbose_mode=True,
        )
    return _decline_dag_metric


# ---------------------------------------------------------------------------
# Parallel API calls
# ---------------------------------------------------------------------------


def _call_api(tc: OANTestCase) -> tuple[str, str | None]:
    # Explicitly use a different, unique session_id for every call
    # ensuring guaranteed single-turn behavior for each dataset entry.
    fresh_session_id = f"eval-{tc.name}-{uuid.uuid4().hex[:8]}"
    
    output = get_client().chat(
        query=tc.input,
        session_id=fresh_session_id,
        user_id=tc.user_id,
        source_lang=tc.source_lang,
        target_lang=tc.target_lang,
    )
    return tc.name, output


def fetch_all_outputs(cases: list[OANTestCase]) -> dict[str, str | None]:
    results: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_call_api, tc): tc for tc in cases}
        for future in as_completed(futures):
            tc = futures[future]
            try:
                name, output = future.result()
                results[name] = output
                print(f"[API] ✓ {name!r}  →  {len(output or '')} chars")
            except Exception as exc:
                results[tc.name] = None
                print(f"[API] ✗ {tc.name!r}  →  {exc}")
    return results


# ---------------------------------------------------------------------------
# pytest parametrisation
# ---------------------------------------------------------------------------


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "tc" not in metafunc.fixturenames:
        return

    if not hasattr(pytest_generate_tests, "_cases"):
        pytest_generate_tests._cases = load_test_cases()

    if not hasattr(pytest_generate_tests, "_cache"):
        print("\n[Setup] Fetching API responses in parallel...")
        pytest_generate_tests._cache = fetch_all_outputs(pytest_generate_tests._cases)

    cases: list[OANTestCase] = pytest_generate_tests._cases
    cache: dict[str, str | None] = pytest_generate_tests._cache

    params = [(tc, cache.get(tc.name)) for tc in cases]
    ids = [tc.name for tc in cases]
    metafunc.parametrize("tc,actual_output", params, ids=ids)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_oan_integration(tc: OANTestCase, actual_output: str | None) -> None:
    assert actual_output, (
        f"[{tc.name}] API returned empty. Check {BASE_URL}/api/health/live"
    )

    case = LLMTestCase(
        input=tc.input,
        actual_output=actual_output,
        expected_output=tc.expected_output,
        retrieval_context=tc.context or None,
    )

    if not tc.is_decline:
        # --- Valid agricultural response: check relevancy + AG_DAG (citation required) ---
        relevancy = get_relevancy_metric()
        ag_dag = get_ag_dag_metric()

        relevancy.measure(case)
        time.sleep(METRIC_CALL_DELAY)
        ag_dag.measure(case)

        print(
            f"\n[{tc.name}]  (valid agricultural)\n"
            f"  Judge model     : {JUDGE_MODEL}\n"
            f"  AnswerRelevancy : {relevancy.score:.3f}  pass={relevancy.is_successful()}\n"
            f"  AG_DAG          : {ag_dag.score:.3f}  pass={ag_dag.is_successful()}\n"
            f"  Relevancy reason: {relevancy.reason}\n"
            f"  AG_DAG reason   : {ag_dag.reason}"
        )

        assert relevancy.is_successful(), (
            f"AnswerRelevancy FAILED ({relevancy.score:.3f} < {RELEVANCY_THRESHOLD})"
            f" — {relevancy.reason}"
        )
        assert ag_dag.is_successful(), (
            f"AG_DAG FAILED ({ag_dag.score:.3f} < {DAG_THRESHOLD}) — {ag_dag.reason}"
        )

    else:
        # --- Decline response: only check polite refusal (no citation needed) ---
        decline_dag = get_decline_dag_metric()
        decline_dag.measure(case)

        print(
            f"\n[{tc.name}]  (decline — DECLINE_DAG only)\n"
            f"  Judge model     : {JUDGE_MODEL}\n"
            f"  DECLINE_DAG     : {decline_dag.score:.3f}  pass={decline_dag.is_successful()}\n"
            f"  DECLINE_DAG reason: {decline_dag.reason}"
        )

        assert decline_dag.is_successful(), (
            f"DECLINE_DAG FAILED ({decline_dag.score:.3f} < {DAG_THRESHOLD}) — {decline_dag.reason}"
        )