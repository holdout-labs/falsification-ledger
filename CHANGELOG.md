# Changelog

## [0.1.4] - 2026-09-06
- feat: `fl conformance` — fail-closed check that an evidence report covers
  every required adjudication criterion declared at pre-registration.  A
  contract may declare `criteria` (name + optional `required`, default
  true) next to the free-form falsification contract; a report may declare
  `criteria_outcomes` (name + `met` / `violated` / `not_tested` /
  `inconclusive`).  A `not_falsified` pass claim on evidence that never
  tested — or failed — a required criterion is blocked; non-required
  criteria and undeclared extra outcomes are informative only.  Schema v1
  gains the optional `criteria_outcomes` array (backward compatible).
- docs: field case `examples/case_verdict_drift.py` — offline reproduction
  of a production drift where a verdict engine passed cases on the primary
  statistic alone although its own pre-registered contract required a
  positive control arm with a passing paired test over aligned windows.

## [0.1.3] - 2026-09-03
- fix: rebuild — the 0.1.2 PyPI build was a stale parallel build (missing
  schema bundling and `fl demo`) and cannot be re-uploaded after deletion;
  0.1.3 ships the schema inside the package and the `fl demo` loop.
- docs: status -> v0.1.3 alpha; llms.txt (machine-readable manifest).

## [0.1.2] - 2026-09-02
- fix: schema `$id` org residue `metabolism-tools` -> `holdout-labs`.
- docs: awesome-quant feature (PR #593, merged 2026-08-29); README.zh-CN; demo GIF rendering fixes.

## [0.1.1] / [0.1.0] - 2026-08-18
- Initial public release: hash-chained pre-registration ledger, falsification contracts, one-shot adjudication, Wilson-CI hit-rate reports.
