"""Tests for adjudication conformance: pre-registered contract criteria vs
the criteria outcomes an evidence report declares.

The motivating failure mode (production, 2026-09): a verdict engine
pre-registered a pass rule that required a control arm to be positive with
a passing paired test over aligned windows, but the implementation passed
cases on the primary statistic alone — the evidence never tested the
control criterion.  ``criteria_conformance`` makes that drift machine-
checkable at the ledger layer: a ``not_falsified`` pass claim on evidence
that never tested (or failed) a required criterion is blocked.
"""

from __future__ import annotations

import json

import pytest

from falsification_ledger.cli import main
from falsification_ledger.contracts import (
    criteria_conformance,
    validate_falsification_report,
)

CONTRACT_WITH_CRITERIA = {
    "kills_when": "oos rank IC p-value > 0.10 with n>=300 bars",
    "criteria": [
        {"name": "primary_positive", "required": True,
         "note": "primary statistic positive and test passes"},
        {"name": "control_superiority", "required": True,
         "note": "control arm positive with a passing paired test"},
        {"name": "window_alignment", "required": False,
         "note": "both arms share the same observation window"},
    ],
}


def _report(**overrides) -> dict:
    report = {
        "schema_version": "falsification_ledger.falsification_report.v1",
        "candidate_artifact_id": "sha256:" + "a" * 64,
        "candidate_type": "mechanism",
        "falsification_type": "oos_rank_ic",
        "null_model": {
            "construction": "permute labels",
            "n_permutations": 1000,
            "distribution": "permutation",
            "random_seed": 42,
            "seed_fixed": True,
        },
        "metrics": [
            {
                "name": "rank_ic",
                "value": 0.03,
                "p_value": 0.04,
                "null_distribution": {"n_permutations": 1000, "seed": 42, "reproducible": True},
            }
        ],
        "conclusion": "not_falsified",
        "program": {"name": "verdict-engine", "version": "1.0.0"},
        "data_window": {"start": "2025-01-01", "end": "2026-08-01"},
        "generated_at": "2026-09-06T12:00:00+08:00",
        "safety_contract": {
            "production_effect": False,
            "changes_probability": False,
            "allow_real_trade": False,
        },
    }
    report.update(overrides)
    return report


# --- no criteria declared: nothing to check ---------------------------------


def test_no_contract_conforms() -> None:
    assert criteria_conformance(None, _report()) == []


def test_contract_without_criteria_conforms() -> None:
    contract = {"kills_when": "anything"}
    assert criteria_conformance(contract, _report()) == []
    assert criteria_conformance(contract, None) == []


# --- malformed contract criteria --------------------------------------------


def test_malformed_criteria_non_list_blocked() -> None:
    assert criteria_conformance({"criteria": "nope"}, _report()) != []
    assert criteria_conformance({"criteria": []}, _report()) != []


def test_malformed_criteria_item_without_name_blocked() -> None:
    contract = {"criteria": [{"required": True}]}
    blockers = criteria_conformance(contract, _report())
    assert any("contract_criteria_malformed" in b for b in blockers)


def test_duplicate_criterion_name_blocked() -> None:
    contract = {"criteria": [{"name": "x"}, {"name": "x"}]}
    blockers = criteria_conformance(contract, _report())
    assert any("duplicate_criterion:x" in b for b in blockers)


def test_non_boolean_required_blocked() -> None:
    contract = {"criteria": [{"name": "x", "required": "yes"}]}
    blockers = criteria_conformance(contract, _report())
    assert any("contract_criteria_malformed" in b for b in blockers)


# --- coverage: every required criterion must be tested ----------------------


def test_drift_case_report_without_outcomes_blocked() -> None:
    """The motivating case: contract declares control_superiority required,
    the report claims not_falsified without declaring any criteria outcome."""
    contract = CONTRACT_WITH_CRITERIA
    report = _report()
    blockers = criteria_conformance(contract, report)
    assert any("criterion_not_covered:primary_positive" in b for b in blockers)
    assert any("criterion_not_covered:control_superiority" in b for b in blockers)
    # window_alignment is not required -> not covered is fine
    assert not any("window_alignment" in b and "not_covered" in b for b in blockers)
    # a pass claim on untested required criteria is doubly blocked
    assert any(
        "not_falsified_while_required_not_met:control_superiority:missing" in b
        for b in blockers
    )


def test_partially_tested_report_blocked() -> None:
    """Control criterion declared but never tested -> the drift is caught."""
    report = _report(
        criteria_outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "window_alignment", "outcome": "met"},
        ]
    )
    blockers = criteria_conformance(CONTRACT_WITH_CRITERIA, report)
    assert any("criterion_not_covered:control_superiority" in b for b in blockers)
    assert any(
        "not_falsified_while_required_not_met:control_superiority:missing" in b
        for b in blockers
    )


def test_non_required_criterion_may_be_untested() -> None:
    report = _report(
        criteria_outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "control_superiority", "outcome": "met"},
        ]
    )
    assert criteria_conformance(CONTRACT_WITH_CRITERIA, report) == []


# --- pass discipline: not_falsified requires all required criteria met ------



def test_required_criterion_not_tested_blocks_pass() -> None:
    report = _report(
        criteria_outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "control_superiority", "outcome": "not_tested"},
        ]
    )
    blockers = criteria_conformance(CONTRACT_WITH_CRITERIA, report)
    assert any(
        "not_falsified_while_required_not_met:control_superiority:not_tested" in b
        for b in blockers
    )


def test_required_criterion_violated_blocks_pass() -> None:
    report = _report(
        criteria_outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "control_superiority", "outcome": "violated"},
        ]
    )
    blockers = criteria_conformance(CONTRACT_WITH_CRITERIA, report)
    assert any(
        "not_falsified_while_required_not_met:control_superiority:violated" in b
        for b in blockers
    )


def test_all_required_met_passes() -> None:
    report = _report(
        criteria_outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "control_superiority", "outcome": "met"},
            {"name": "window_alignment", "outcome": "not_tested"},
        ]
    )
    assert criteria_conformance(CONTRACT_WITH_CRITERIA, report) == []


def test_inconclusive_conclusion_not_forced_to_pass_discipline() -> None:
    """An inconclusive report is missing evidence — no pass claim, so the
    not_falsified discipline does not apply; coverage still applies."""
    report = _report(
        conclusion="inconclusive",
        criteria_outcomes=[{"name": "primary_positive", "outcome": "inconclusive"}],
    )
    blockers = criteria_conformance(CONTRACT_WITH_CRITERIA, report)
    assert any("criterion_not_covered:control_superiority" in b for b in blockers)
    assert not any("not_falsified_while_required_not_met" in b for b in blockers)


def test_extra_undeclared_outcome_allowed() -> None:
    report = _report(
        criteria_outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "control_superiority", "outcome": "met"},
            {"name": "exploratory_extra", "outcome": "violated"},
        ]
    )
    assert criteria_conformance(CONTRACT_WITH_CRITERIA, report) == []


def test_duplicate_outcome_blocked() -> None:
    report = _report(
        criteria_outcomes=[
            {"name": "primary_positive", "outcome": "met"},
            {"name": "primary_positive", "outcome": "violated"},
        ]
    )
    blockers = criteria_conformance(CONTRACT_WITH_CRITERIA, report)
    assert any("duplicate_outcome:primary_positive" in b for b in blockers)


def test_malformed_outcome_value_blocked() -> None:
    report = _report(
        criteria_outcomes=[{"name": "primary_positive", "outcome": "probably"}]
    )
    blockers = criteria_conformance(CONTRACT_WITH_CRITERIA, report)
    assert any("report_criteria_outcomes_malformed" in b for b in blockers)


# --- schema: criteria_outcomes is optional and validated --------------------


def test_schema_accepts_valid_criteria_outcomes() -> None:
    report = _report(
        criteria_outcomes=[
            {"name": "control_superiority", "outcome": "met", "note": "paired t p=0.02"}
        ]
    )
    assert validate_falsification_report(report) == []


def test_schema_rejects_invalid_outcome_value() -> None:
    report = _report(criteria_outcomes=[{"name": "x", "outcome": "maybe"}])
    blockers = validate_falsification_report(report)
    assert any("outcome" in blocker for blocker in blockers)


def test_schema_accepts_report_without_criteria_outcomes() -> None:
    assert validate_falsification_report(_report()) == []


# --- CLI: fl conformance ----------------------------------------------------


@pytest.fixture()
def state(tmp_path):
    return str(tmp_path / "ledger")


def _write(path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_cli_conformance_blocks_drift(state, tmp_path, capsys) -> None:
    """Pre-register a contract with criteria, then hand conformance a
    not_falsified report that never tested the required control criterion."""
    main(["init", "--state-dir", state])
    contract_path = tmp_path / "contract.json"
    _write(contract_path, CONTRACT_WITH_CRITERIA)
    assert (
        main(
            [
                "preregister",
                "--state-dir", state,
                "--case-id", "CASE-DRIFT",
                "--verdict", "support",
                "--reason", "mechanism survives OOS",
                "--contract", str(contract_path),
            ]
        )
        == 0
    )
    report_path = tmp_path / "report.json"
    _write(report_path, _report())
    capsys.readouterr()
    assert (
        main(
            [
                "conformance",
                "--state-dir", state,
                "--case-id", "CASE-DRIFT",
                "--report", str(report_path),
            ]
        )
        == 1
    )
    body = json.loads(capsys.readouterr().out)
    assert body["conformance"] == "blocked"
    assert any("criterion_not_covered:control_superiority" in b
               for b in body["conformance_blockers"])
    assert body["criteria_declared"][1]["name"] == "control_superiority"


def test_cli_conformance_passes_when_required_criteria_met(state, tmp_path, capsys) -> None:
    main(["init", "--state-dir", state])
    contract_path = tmp_path / "contract.json"
    _write(contract_path, CONTRACT_WITH_CRITERIA)
    main(
        [
            "preregister",
            "--state-dir", state,
            "--case-id", "CASE-OK",
            "--verdict", "support",
            "--reason", "mechanism survives OOS",
            "--contract", str(contract_path),
        ]
    )
    report_path = tmp_path / "report.json"
    _write(
        report_path,
        _report(
            criteria_outcomes=[
                {"name": "primary_positive", "outcome": "met"},
                {"name": "control_superiority", "outcome": "met"},
            ]
        ),
    )
    capsys.readouterr()
    assert (
        main(
            [
                "conformance",
                "--state-dir", state,
                "--case-id", "CASE-OK",
                "--report", str(report_path),
            ]
        )
        == 0
    )
    body = json.loads(capsys.readouterr().out)
    assert body["conformance"] == "pass"
    assert body["schema_blockers"] == []
    assert body["conformance_blockers"] == []


def test_cli_conformance_requires_registration(state, tmp_path, capsys) -> None:
    main(["init", "--state-dir", state])
    report_path = tmp_path / "report.json"
    _write(report_path, _report())
    capsys.readouterr()
    assert (
        main(
            [
                "conformance",
                "--state-dir", state,
                "--case-id", "NEVER-REGISTERED",
                "--report", str(report_path),
            ]
        )
        == 1
    )
    body = json.loads(capsys.readouterr().out)
    assert body["registered"] is False
    assert "register_required" in body["error"]


def test_cli_verify_chain_intact_after_conformance_flow(state, tmp_path) -> None:
    """The conformance flow must leave the ledger hash chain intact."""
    main(["init", "--state-dir", state])
    contract_path = tmp_path / "contract.json"
    _write(contract_path, CONTRACT_WITH_CRITERIA)
    assert (
        main(
            [
                "preregister",
                "--state-dir", state,
                "--case-id", "CASE-CHAIN",
                "--verdict", "support",
                "--reason", "chain stays intact",
                "--contract", str(contract_path),
            ]
        )
        == 0
    )
    report_path = tmp_path / "report.json"
    _write(report_path, _report())
    assert (
        main(
            [
                "conformance",
                "--state-dir", state,
                "--case-id", "CASE-CHAIN",
                "--report", str(report_path),
            ]
        )
        == 1
    )
    assert main(["verify", "--state-dir", state]) == 0
