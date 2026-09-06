"""Falsification report contract: schema validation, content IDs, evidence status.

A falsification report is the machine-readable evidence that a pre-registered
claim survived (or did not survive) an independent check.  The contract:

- validates reports against ``schema/falsification-report.schema.json``
  (fail-closed: blockers are returned, never silently tolerated),
- computes a content ID (``sha256:<digest>``) over the canonical bytes so
  reports are addressable and tamper-evident,
- classifies evidence status for gates: ``valid`` / ``invalid`` /
  ``missing`` (a falsified or inconsistent report counts as *missing*
  evidence — absence of proof is not proof of absence, but it is not
  support either).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_FILE = "falsification-report.schema.json"
SCHEMA_VERSION = "falsification_ledger.falsification_report.v1"
DOMAIN_PREFIX = "falsification-ledger/falsification-report.v1"
CONSISTENCY_TOLERANCE = 0.005
CRITERION_OUTCOMES = ("met", "violated", "not_tested", "inconclusive")

_schema_cache: dict[str, Any] | None = None


def _default_schema_text() -> str:
    """Read the bundled schema, wherever the package was installed from.

    Prefers the schema shipped inside the package (wheel/sdist since 0.1.2),
    then falls back to the repository layout (``<repo>/schema/``) so an
    editable checkout keeps working unchanged.
    """
    try:
        from importlib.resources import files

        return files("falsification_ledger").joinpath("schema", SCHEMA_FILE).read_text()
    except Exception:
        repo = Path(__file__).resolve().parents[2] / "schema" / SCHEMA_FILE
        return repo.read_text(encoding="utf-8")


def load_falsification_schema(schema_path: Path | str | None = None) -> dict[str, Any]:
    """Load and sanity-check the falsification report schema (cached)."""
    global _schema_cache
    if _schema_cache is None or schema_path is not None:
        if schema_path is not None:
            raw = Path(schema_path).read_text(encoding="utf-8")
        else:
            raw = _default_schema_text()
        schema = json.loads(raw)
        jsonschema.Draft202012Validator.check_schema(schema)
        _schema_cache = schema
    return _schema_cache


def falsification_object_id(value: dict[str, Any]) -> str:
    """Content ID: sha256(domain_prefix || 0x00 || canonical bytes)."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(
        DOMAIN_PREFIX.encode("utf-8") + b"\x00" + payload
    ).hexdigest()
    return f"sha256:{digest}"


def validate_falsification_report(
    value: dict[str, Any],
    schema_path: Path | str | None = None,
) -> list[str]:
    """Return blockers; an empty list means the report conforms to the contract."""
    schema = load_falsification_schema(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    blockers: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in error.path) or "$"
        blockers.append(f"{path}: {error.message}")
    if not blockers and value.get("schema_version") != SCHEMA_VERSION:
        blockers.append(f"schema_version mismatch: {value.get('schema_version')!r}")
    if not blockers:
        consistency = value.get("consistency")
        if consistency is not None:
            if consistency.get("tolerance") != CONSISTENCY_TOLERANCE:
                blockers.append(
                    f"consistency.tolerance must be {CONSISTENCY_TOLERANCE}, "
                    f"got {consistency.get('tolerance')!r}"
                )
    return blockers


def evidence_status(
    value: dict[str, Any],
    schema_path: Path | str | None = None,
) -> str:
    """Evidence status for gates (fail-closed).

    - ``invalid``  — report does not conform to the contract, or the
      conclusion is falsified, or consistency is explicitly broken.
    - ``missing``  — inconclusive: treated as absent evidence.
    - ``valid``    — conformant, not falsified, consistent.
    """
    blockers = validate_falsification_report(value, schema_path)
    if blockers:
        return "invalid"
    conclusion = value.get("conclusion")
    if conclusion == "falsified":
        return "invalid"
    if conclusion == "inconclusive":
        return "missing"
    consistency = value.get("consistency")
    if consistency is not None and consistency.get("consistent") is False:
        return "invalid"
    return "valid"


def criteria_conformance(
    contract: dict[str, Any] | None,
    report: dict[str, Any] | None,
) -> list[str]:
    """Fail-closed conformance check between the adjudication criteria a
    contract declares and the criteria outcomes an evidence report declares.

    A falsification contract may declare, next to its free-form description,
    a structured ``criteria`` list — every condition that must hold for the
    claim to survive review (a control arm outperforming with a passing
    paired test, a minimum completed-sample count, a fixed observation
    window, ...).  A falsification report may then declare
    ``criteria_outcomes``: which of those criteria were actually tested and
    with what result.  The check exists to catch the drift where a pass
    claim (``conclusion: not_falsified``) rests on evidence that never
    tested every criterion the researcher promised to test.

    Rules (fail-closed; blockers are returned, never silently tolerated):

    - ``contract.criteria``, when present, must be a non-empty array of
      objects with a non-empty string ``name`` and an optional boolean
      ``required`` (default ``true``);
    - ``report.criteria_outcomes``, when present, must be an array of
      objects with a non-empty string ``name`` and an outcome in
      ``CRITERION_OUTCOMES``; names must not repeat;
    - every *required* criterion of the contract must have exactly one
      outcome in the report (missing -> ``criterion_not_covered``);
    - a report concluding ``not_falsified`` while any required criterion
      outcome is not ``met`` is blocked
      (``not_falsified_while_required_not_met``);
    - non-required criteria and extra undeclared outcomes are informative
      only: they never block;
    - a contract without ``criteria`` declares nothing to check and always
      conforms.

    Returns blocker strings; an empty list means conformant.
    """
    blockers: list[str] = []
    if not isinstance(contract, dict):
        return blockers
    criteria = contract.get("criteria")
    if criteria is None:
        return blockers

    declared: list[tuple[str, bool]] = []
    seen: set[str] = set()
    if not isinstance(criteria, list) or not criteria:
        return ["contract_criteria_malformed: expected a non-empty array"]
    for index, item in enumerate(criteria):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
        ):
            blockers.append(
                f"contract_criteria_malformed: item {index} needs a "
                "non-empty string 'name'"
            )
            continue
        name = item["name"].strip()
        if name in seen:
            blockers.append(f"duplicate_criterion:{name}")
        seen.add(name)
        required = item.get("required", True)
        if not isinstance(required, bool):
            blockers.append(
                f"contract_criteria_malformed: criterion {name!r} "
                "'required' must be boolean"
            )
            required = True
        declared.append((name, required))
    if blockers:
        return blockers

    outcomes: dict[str, str] = {}
    raw_outcomes = report.get("criteria_outcomes") if isinstance(report, dict) else None
    if raw_outcomes is not None:
        if not isinstance(raw_outcomes, list):
            return ["report_criteria_outcomes_malformed: expected an array"]
        for index, item in enumerate(raw_outcomes):
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"].strip()
            ):
                blockers.append(
                    f"report_criteria_outcomes_malformed: item {index} needs "
                    "a non-empty string 'name'"
                )
                continue
            name = item["name"].strip()
            outcome = item.get("outcome")
            if outcome not in CRITERION_OUTCOMES:
                blockers.append(
                    f"report_criteria_outcomes_malformed: invalid outcome "
                    f"{outcome!r} for {name!r}"
                )
                continue
            if name in outcomes:
                blockers.append(f"duplicate_outcome:{name}")
            outcomes[name] = outcome
        if blockers:
            return blockers

    conclusion = report.get("conclusion") if isinstance(report, dict) else None
    for name, required in declared:
        outcome = outcomes.get(name)
        if required and outcome is None:
            blockers.append(f"criterion_not_covered:{name}")
        if conclusion == "not_falsified" and required and outcome != "met":
            blockers.append(
                f"not_falsified_while_required_not_met:{name}:"
                f"{outcome or 'missing'}"
            )
    return blockers
