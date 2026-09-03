"""Command-line interface for falsification-ledger.

Subcommands:

- ``init``                 create the ledger state directory
- ``preregister``          register a claim (verdict + reason) before evidence;
                           optionally attach a falsification contract JSON
- ``submit``               validate a falsification report and print its
                           content ID and evidence status (read-only)
- ``adjudicate``           backfill the actual verdict for a case
- ``report``               hit-rate report (Wilson 95% CI vs random baseline)
- ``verify``               hash-chain integrity check of the whole ledger
- ``demo``                 run the full loop on a scratch ledger, then prove
                           tamper detection (60-second intro, no setup)
- ``version``              print version
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .contracts import evidence_status, falsification_object_id, validate_falsification_report
from .ledger import (
    conclude_prediction,
    ledger_path,
    register_prediction,
    report_prediction_hitrate,
    verify_chain,
)


def _print_json(body: dict[str, Any]) -> None:
    print(json.dumps(body, ensure_ascii=False, indent=2))


def _load_json_file(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fl",
        description="Pre-registration and falsification ledger for research.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the ledger state directory")
    init.add_argument("--state-dir", required=True, help="ledger state directory")

    prereg = sub.add_parser("preregister", help="register a claim before evidence")
    prereg.add_argument("--state-dir", required=True)
    prereg.add_argument("--case-id", required=True)
    prereg.add_argument(
        "--verdict", required=True, choices=["support", "against", "uncertain"]
    )
    prereg.add_argument("--reason", required=True)
    prereg.add_argument("--source-type", default="other",
                        choices=["paper", "business", "cross_domain", "pipeline", "other"])
    prereg.add_argument("--contract", default=None,
                        help="falsification contract JSON: what evidence would kill the claim")

    submit = sub.add_parser("submit", help="validate a falsification report (read-only)")
    submit.add_argument("--report", required=True, help="falsification report JSON path")
    submit.add_argument("--schema", default=None, help="override schema JSON path")

    adjudicate = sub.add_parser("adjudicate", help="backfill the actual verdict")
    adjudicate.add_argument("--state-dir", required=True)
    adjudicate.add_argument("--case-id", required=True)
    adjudicate.add_argument(
        "--verdict", required=True, choices=["support", "against", "uncertain"]
    )

    report = sub.add_parser("report", help="hit-rate report")
    report.add_argument("--state-dir", required=True)
    report.add_argument("--min-cases", type=int, default=20)

    verify = sub.add_parser("verify", help="hash-chain integrity check")
    verify.add_argument("--state-dir", required=True)

    sub.add_parser(
        "demo",
        help=(
            "run the full loop on a scratch ledger, then prove tamper "
            "detection (no setup needed)"
        ),
    )

    sub.add_parser("version", help="print version")
    return parser


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_demo() -> int:
    """Run the full ledger loop on a scratch ledger, then prove tamper detection.

    The whole point of the tool in 60 seconds: pre-register -> evidence ->
    honest verdict -> hit rate, then break one field and watch ``fl verify``
    name the exact line.  Safe to re-run; writes only under a temp dir.
    """
    state = Path(tempfile.mkdtemp(prefix="fl-demo-state-"))
    files = Path(tempfile.mkdtemp(prefix="fl-demo-files-"))
    ledger = ledger_path(state)

    def step(title: str) -> None:
        print(f"\n== {title} ==")

    step("0. init — the ledger is one append-only JSONL file")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    print(f"ledger: {ledger}")

    step("1. preregister — BEFORE the study: what you expect + what would kill it")
    contract = {
        "kills_when": "oos rank IC p-value > 0.10 with n>=300 bars",
        "power_target": 0.8,
        "preregistered_before_evidence": True,
    }
    _write_json(files / "contract.json", contract)
    print(
        json.dumps(
            register_prediction(
                state,
                "MOMENTUM-OOS-2026Q3",
                "support",
                "momentum rank IC stays positive out-of-sample",
                "paper",
                falsification_contract=contract,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )

    step("2. submit — an independent falsification report (content-addressed)")
    report = {
        "schema_version": "falsification_ledger.falsification_report.v1",
        "candidate_artifact_id": "sha256:" + "a" * 64,
        "candidate_type": "factor",
        "falsification_type": "null_model_randomization",
        "null_model": {
            "construction": "permute labels within session",
            "n_permutations": 1000,
            "distribution": "permutation",
            "random_seed": 42,
            "seed_fixed": True,
        },
        "metrics": [
            {
                "name": "oos_rank_ic",
                "value": 0.031,
                "p_value": 0.04,
                "null_distribution": {"n_permutations": 1000, "seed": 42, "reproducible": True},
            }
        ],
        "conclusion": "not_falsified",
        "program": {"name": "oos-null-check", "version": "1.0.0"},
        "data_window": {"start": "2025-01-01", "end": "2026-08-01"},
        "generated_at": "2026-08-18T12:00:00+08:00",
        "safety_contract": {
            "production_effect": False,
            "changes_probability": False,
            "allow_real_trade": False,
        },
    }
    _write_json(files / "report.json", report)
    print(
        json.dumps(
            {
                "content_id": falsification_object_id(report),
                "evidence_status": evidence_status(report),
                "blockers": validate_falsification_report(report),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    step("3. adjudicate — one honest verdict AFTER the study")
    print(
        json.dumps(
            conclude_prediction(state, "MOMENTUM-OOS-2026Q3", "support"),
            ensure_ascii=False,
            indent=2,
        )
    )

    step("4. report — hit rate with Wilson 95% CI vs the random baseline")
    print(json.dumps(report_prediction_hitrate(state), ensure_ascii=False, indent=2))

    step("5. verify — the hash chain is intact")
    body = verify_chain(state)
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print("=> ledger intact:", body.get("ok"))

    step("6. tamper with one field, then verify again")
    lines = ledger.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["expected_reason"] = "rewritten after the fact (oops)"
    lines[0] = json.dumps(event, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    body = verify_chain(state)
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print(
        "=> edit detected at line",
        body.get("first_bad_line"),
        "(verify exits 1 — exactly as designed)",
    )
    print(f"\nscratch ledger left at: {state}  (delete anytime)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "init":
        path = ledger_path(args.state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"init: ledger ready at {path}")
        return 0

    if args.command == "preregister":
        contract = _load_json_file(args.contract) if args.contract else None
        result = register_prediction(
            args.state_dir,
            args.case_id,
            args.verdict,
            args.reason,
            args.source_type,
            falsification_contract=contract,
        )
        _print_json(result)
        return 0

    if args.command == "submit":
        report = _load_json_file(args.report)
        blockers = validate_falsification_report(report, args.schema)
        body = {
            "content_id": falsification_object_id(report),
            "evidence_status": evidence_status(report, args.schema),
            "blockers": blockers,
        }
        _print_json(body)
        return 0 if not blockers else 1

    if args.command == "adjudicate":
        _print_json(conclude_prediction(args.state_dir, args.case_id, args.verdict))
        return 0

    if args.command == "report":
        _print_json(report_prediction_hitrate(args.state_dir, min_cases=args.min_cases))
        return 0

    if args.command == "verify":
        body = verify_chain(args.state_dir)
        _print_json(body)
        return 0 if body.get("ok") else 1

    if args.command == "demo":
        return cmd_demo()

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
