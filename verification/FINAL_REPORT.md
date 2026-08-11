# TreePact M9 Consolidated Verification Report

- Candidate: `3c3ba04fa49f08e0` (frozen 2026-08-11T15:53:25.662951+00:00)
- Source tree SHA-256: `3c3ba04fa49f08e086d4f74d4b1e986c1f41f556d6db1f532f32dea0c113a428`
- uv.lock SHA-256: `2cb9fb532ea86ebc19bcd5bacb73a72ad5bb663d6551aa5085ca11bab5c8ccc5`
- Migration head: 1
- Python: 3.12.13 on Darwin arm64
- OpenCode: 1.18.15
- Git commit: none recorded (repository intentionally not a Git repo; no-commit constraint)

## Verdict

**GO**

No open failures across all nine stages.

## Stage results

- static.ruff: PASS (0.0s)
- static.mypy: PASS (0.9s)
- static.pip-audit: PASS (1.8s)
- static.lockfile: PASS (0.0s)
- static.compile: PASS (0.0s)
- unit: PASS (0.9s)
- integration: PASS (5.1s)
- adversarial: PASS (6.9s)
- recovery: PASS (1.4s)
- resources: PASS (2.3s)
- evals: PASS (5.6s)
- runtime: 0 (match=True)
- audit: 54 traceability rows

## Honest limitations

- M9 used synthetic fixture repositories for EVAL-001..004; real-repository
  validation is the M10 internal pilot (MASTER_PLAN.md M10).
- The OpenCode adapter ceiling is TP2 (ADR 0017): the defensive plugin
  requires bun, which is deferred; tool restriction is config-level.
- Native checks run under `trusted_harness`, never described as sandboxed;
  no strong host-isolation claim is made.
- Tests passing is not proof the product is correct; acceptance means
  the exact change satisfied the exact Pact snapshot under the recorded
  environment.

## Release gate

The candidate may enter the M10 internal pilot.
