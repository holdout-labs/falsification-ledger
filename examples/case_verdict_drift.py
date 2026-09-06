"""Reproduce the 2026-09 "verdict that never checked its own contract" case
(offline).

A production shadow-verdict engine pre-registered its pass rule: a case
passes only when (1) the primary statistic is positive with a passing test
*and* (2) the control arm is positive with a passing paired test over
aligned windows.  The implementation drifted: it passed cases on criterion
(1) alone — the evidence never tested the control criterion, so pass claims
rested on a contract that was never checked.

This script rebuilds the same shape with a pre-registered contract and three
evidence reports, and shows what ``criteria_conformance`` (0.1.4+) does:

1. a ``not_falsified`` report with no criteria outcomes at all -> BLOCKED
   (every required criterion uncovered — the drift as shipped);
2. a report that declares the primary outcome but never tests the required
   control criterion -> BLOCKED (``criterion_not_covered``);
3. an honest report declaring per-criterion outcomes, all required criteria
   met -> PASS, evidence ``valid``.

Run with:  python examples/case_verdict_drift.py
No network, no third-party dependencies.  Exits 1 when any of the three
behaviours does not hold (self-check).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from falsification_ledger.contracts import (  # noqa: E402
    criteria_conformance,
    evidence_status,
    validate_falsification_report,
)
from falsification_ledger.ledger import (  # noqa: E402
    prediction_contract,
    register_prediction,
)

CONTRACT = {
    "kills_when": "primary statistic not positive, or control arm not "
                  "positive with a passing paired test over aligned windows",
    "criteria": [
        {"name": "primary_positive", "required": True,
         "note": "primary statistic positive and its test passes"},
        {"name": "control_superiority", "required": True,
         "note": "control arm positive with a passing paired test "
                 "(same calendar window, same sample)"},
        {"name": "sample_complete", "required": False,
         "note": "sample counted as completed trades, not order records"},
    ],
}


def _report(*, outcomes=None, conclusion="not_falsified") -> dict:
    report = {
        "schema_version": "falsification_ledger.falsification_report.v1",
        "candidate_artifact_id": "sha256:" + "b" * 64,
        "candidate_type": "mechanism",
        "falsification_type": "oos_rank_ic",
        "null_model": {
            "construction": "permute arm labels within aligned windows",
            "n_permutations": 1000,
            "distribution": "permutation",
            "random_seed": 7,
            "seed_fixed": True,
        },
        "metrics": [
            {
                "name": "primary_daily_mean_diff",
                "value": 0.009,
                "p_value": 0.03,
                "null_distribution": {
                    "n_permutations": 1000, "seed": 7, "reproducible": True,
                },
            }
        ],
        "conclusion": conclusion,
        "program": {"name": "shadow-verdict-engine", "version": "1.0.0"},
        "data_window": {"start": "2025-01-01", "end": "2026-08-31"},
        "generated_at": "2026-09-06T12:00:00+08:00",
        "safety_contract": {
            "production_effect": False,
            "changes_probability": False,
            "allow_real_trade": False,
        },
    }
    if outcomes is not None:
        report["criteria_outcomes"] = outcomes
    return report


def main() -> int:
    state = Path(tempfile.mkdtemp(prefix="fl-case-verdict-drift-"))
    register_prediction(
        state,
        "VERDICT-DRIFT-2026Q3",
        "support",
        "mechanism survives out-of-sample review",
        "pipeline",
        falsification_contract=CONTRACT,
    )
    contract = prediction_contract(state, "VERDICT-DRIFT-2026Q3")
    print("contract criteria:", json.dumps(contract["criteria"], ensure_ascii=False))

    checks_ok = True

    # 1. the drifted report: pass claim, no criteria outcomes at all.
    drifted = _report()
    blockers = criteria_conformance(contract, drifted)
    blocked = any("criterion_not_covered:control_superiority" in b for b in blockers)
    print(
        "\n1. not_falsified report WITHOUT criteria outcomes  ->",
        "BLOCKED" if blocked else "NOT BLOCKED (regression)",
    )
    for b in blockers:
        print("   -", b)
    checks_ok = checks_ok and blocked

    # 2. partial: primary tested, required control criterion never tested.
    partial = _report(
        outcomes=[{"name": "primary_positive", "outcome": "met"}]
    )
    blockers = criteria_conformance(contract, partial)
    blocked = any(
        "not_falsified_while_required_not_met:control_superiority:missing" in b
        for b in blockers
    )
    print(
        "\n2. control criterion declared but never tested            ->",
        "BLOCKED" if blocked else "NOT BLOCKED (regression)",
    )
    for b in blockers:
        print("   -", b)
    checks_ok = checks_ok and blocked

    # 3. honest: every required criterion tested and met; extra non-required
    #    criterion may stay untested (informative only).
    honest = _report(
        outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "control_superiority", "outcome": "met",
             "note": "paired t over aligned windows, p=0.02"},
            {"name": "sample_complete", "outcome": "not_tested"},
        ]
    )
    blockers = criteria_conformance(contract, honest)
    schema_blockers = validate_falsification_report(honest)
    passed = not blockers and not schema_blockers
    print(
        "\n3. all required criteria tested and met                   ->",
        "PASS" if passed else "NOT PASS (regression)",
    )
    print("   schema blockers:", schema_blockers or "none")
    print("   conformance blockers:", blockers or "none")
    print("   evidence_status:", evidence_status(honest))
    checks_ok = checks_ok and passed

    print(
        "\nverdict:",
        "conformance catches the drift"
        if checks_ok
        else "FAILED — conformance did not behave as expected",
    )
    return 0 if checks_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
