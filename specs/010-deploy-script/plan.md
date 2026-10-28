# Plan: One-command deploy

Spec: ./spec.md

## Design

Rewrite `deploy.ps1` (it was UTF-16; the new file is ASCII/UTF-8) as a parameterised pipeline of steps. `scripts/build-agent-image.ps1` now emits the image URI on the output stream so deploy can capture it. Harness sync uses the Harness REST API (`/pipeline/api/pipelines/v2`, `/pipeline/api/pipeline/execute/<id>`) with `x-api-key`.

## IAM (least privilege)

No new IAM. Uses the caller's AWS profile.

## Terraform changes

None.

## Constitution check

- [x] One Lambda per endpoint
- [x] Least-privilege IAM
- [x] No secrets in repo
- [x] Log group with retention

## Risks / alternatives

Harness API paths are untested without an account. Apply keeps the typed confirmation.
