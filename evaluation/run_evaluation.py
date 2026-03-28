"""
Agentic Evaluation — Risk Auditor Golden Dataset.

Runs 20 hand-crafted test cases through the Risk Auditor node using DeepEval
and prints a success-rate report.  No real database, external API, or LLM key
is required — all I/O is mocked via unittest.mock.

Usage
-----
    python evaluation/run_evaluation.py
    # or from the repo root:
    python -m evaluation.run_evaluation
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, List
from unittest.mock import patch

# Opt out of DeepEval telemetry so the script never blocks waiting for a login
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

# Add the repo root to sys.path so project modules are importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from deepeval import evaluate                          # noqa: E402
from deepeval.test_case import LLMTestCase             # noqa: E402
from deepeval.metrics import BaseMetric                # noqa: E402

from evaluation.golden_dataset import GOLDEN_CASES    # noqa: E402
import main as finance_main                            # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Custom DeepEval metric — deterministic, no LLM required
# ──────────────────────────────────────────────────────────────────────────────

class RiskAssessmentAccuracyMetric(BaseMetric):
    """
    Validates that the Risk Auditor produces the expected ``risk_score`` and
    ``auto_rejected`` flag for each golden-dataset case.

    Score = 1.0 when both values match; 0.0 otherwise.
    """

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold
        self.score: float = 0.0
        self.reason: str = ""
        self.name = "RiskAssessmentAccuracy"

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        actual: dict = json.loads(test_case.actual_output)
        expected: dict = json.loads(test_case.expected_output)

        issues: List[str] = []
        if actual["risk_score"] != expected["risk_score"]:
            issues.append(
                f"risk_score expected={expected['risk_score']} "
                f"got={actual['risk_score']}"
            )
        if actual["auto_rejected"] != expected["auto_rejected"]:
            issues.append(
                f"auto_rejected expected={expected['auto_rejected']} "
                f"got={actual['auto_rejected']}"
            )

        self.score = 0.0 if issues else 1.0
        self.reason = "; ".join(issues) if issues else "All assertions passed"
        return self.score

    async def a_measure(
        self, test_case: LLMTestCase, *args: Any, **kwargs: Any
    ) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.score >= self.threshold


# ──────────────────────────────────────────────────────────────────────────────
# Case runner — mocks DB so no real SQLite is needed
# ──────────────────────────────────────────────────────────────────────────────

def _run_case(case: dict) -> dict:
    """Run one golden-dataset case through the Risk Auditor node (DB mocked)."""
    state: dict = {
        "refund_request": dict(case["input"]),
        "transaction_verified": case["transaction_verified"],
        "risk_score": 0,
        "audit_logs": [],
        "status": "investigating",
        "request_id": 9999,   # synthetic ID — DB operations are patched below
        "stripe_refund_id": None,
    }
    with (
        patch(
            "database.get_monthly_refund_count",
            return_value=case["monthly_refund_count"],
        ),
        patch("database.log_audit"),
        patch("database.update_refund_status"),
        patch("main._get_groq_llm", return_value=None),  # skip LLM during eval
    ):
        result = finance_main.risk_auditor_node(state)

    return {
        "risk_score": result["risk_score"],
        "auto_rejected": result.get("status") == "rejected",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Build DeepEval test cases
# ──────────────────────────────────────────────────────────────────────────────

def build_test_cases() -> List[LLMTestCase]:
    """Convert each golden-dataset entry into a DeepEval LLMTestCase."""
    cases: List[LLMTestCase] = []
    for case in GOLDEN_CASES:
        actual = _run_case(case)
        expected = {
            "risk_score": case["expected_risk_score"],
            "auto_rejected": case["expected_auto_rejected"],
        }
        cases.append(
            LLMTestCase(
                input=case["label"],
                actual_output=json.dumps(actual),
                expected_output=json.dumps(expected),
            )
        )
    return cases


# ──────────────────────────────────────────────────────────────────────────────
# Main — run evaluation and print success-rate summary
# ──────────────────────────────────────────────────────────────────────────────

def run_evaluation() -> None:
    """Run all golden-dataset cases and print a success-rate report."""
    print("\n" + "=" * 64)
    print("  Risk Auditor — Agentic Evaluation (DeepEval)")
    print("=" * 64)
    print(f"  Running {len(GOLDEN_CASES)} golden-dataset test cases…\n")

    test_cases = build_test_cases()
    metric = RiskAssessmentAccuracyMetric()

    # Evaluate each case and capture results for the custom summary
    results: List[dict] = []
    for tc in test_cases:
        metric.measure(tc)
        results.append(
            {
                "label": tc.input,
                "passed": metric.is_successful(),
                "reason": metric.reason,
                "actual": json.loads(tc.actual_output),
                "expected": json.loads(tc.expected_output),
            }
        )

    # Run DeepEval's native evaluation (generates its own formatted report)
    evaluate(test_cases=test_cases, metrics=[metric], print_results=True)

    # ── Custom success-rate summary ───────────────────────────────────────────
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    pct = 100.0 * passed / total if total else 0.0

    print("\n" + "─" * 64)
    print(f"  SUCCESS RATE: {passed}/{total} ({pct:.1f}%)")
    print("─" * 64)

    if passed < total:
        print("\n  ✗ FAILED CASES:")
        for r in results:
            if not r["passed"]:
                print(f"    • {r['label']}")
                print(f"      Expected : {r['expected']}")
                print(f"      Actual   : {r['actual']}")
                print(f"      Reason   : {r['reason']}")

    print()


if __name__ == "__main__":
    run_evaluation()
