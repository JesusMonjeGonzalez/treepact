# ADR 0019: Repository Secret Scanning Only

- Status: accepted (explicit operator authorization)
- Date: 2026-09-17
- Supersession: narrow exception to ADR 0012 for this repository's secret scan;
  all product CI/CD restrictions remain in force.

## Context

The operator explicitly requested repository secret scanning in GitHub Actions.
ADR 0012 limits TreePact's functional scope to local pre-merge supervision; its
blanket workflow prohibition needs a documented exception for repository hygiene.
This is not a new TreePact runtime capability or a replacement for local evidence.

## Decision

Allow **repository secret scanning only, no product CI/CD capability**.
The sole workflow is `.github/workflows/secrets.yml`, running Gitleaks on pushes,
pull requests and manual dispatch. It may use a GitHub-hosted ephemeral runner
only for checkout, verified scanner download and secret scanning.

- Pin checkout to an upstream commit and Gitleaks to a version plus SHA-256.
- Grant only `contents: read`; never persist checkout credentials.
- Scan full fetched history with complete secret redaction and fail on findings
  or scanner errors. Do not upload reports or post PR comments.
- Do not use `pull_request_target`, execute project code, run product tests,
  build packages, publish, deploy or provide runtime pipeline controls.
- Exceptions for proven synthetic fixtures must use exact historical fingerprints,
  with documented evidence; no broad path or rule allowlists.

Creating these files does not authorize launching a remote run, provisioning
runners, changing branch protection, committing or pushing. Future workflow runs
will follow the configured events after an operator separately publishes it.

## Consequences

ADR 0011's local verification campaign and ADR 0014's publication prohibitions
remain unchanged. GitHub secret scanning is repository maintenance, not evidence
that TreePact passes product gates. Unfetched refs, unreachable objects and
uncommitted data remain outside the history scan. Repository Actions settings
and required checks need a separate owner review.

## Alternatives

- Local-only scanning: retained as a supported command, but does not satisfy the
  operator's explicit request for repository pipeline security.
- General product CI/CD: rejected; outside this exception and ADR 0012's scope.
