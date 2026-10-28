# Spec: One-command deploy via deploy.ps1

Status: Approved (requested by user)

## Problem / Why

Deploying needs several manual steps: tests, agent image build, Terraform, HTML upload, Transaction Search, evals, Harness pipeline.

## Acceptance criteria

- AC1: `deploy.ps1` runs, in order: tests and Terraform checks, optional agent image build (`-BuildImage`), `terraform plan`, a typed `APPLY` confirmation, apply, HTML upload (changed files only), Transaction Search enable, evals (`-RunEvals`), Harness pipeline sync and trigger (`-Harness`).
- AC2: Apply never runs without the typed `APPLY` confirmation; `-SkipApply` stops after plan.
- AC3: Any failing step stops the script with a non-zero exit code.
- AC4: No secrets in the script; Harness uses `HARNESS_API_KEY` and `HARNESS_ACCOUNT_ID` from the environment (plus `HARNESS_ORG`, `HARNESS_PROJECT`).
- AC5: A newly built image URI is passed to Terraform and written back to `infra/env/dev.tfvars` after a successful apply.

## Out of scope

Destroy, backend changes, creating the Harness account/delegate.

## Open questions

None.
