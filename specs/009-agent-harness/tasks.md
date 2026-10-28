# Tasks: Agent harness

- [ ] T1 Write failing tests: `tests/test_run_evals.py`, `tests/test_agent_otel.py`
- [ ] T2 Add `aws-opentelemetry-distro`; update Dockerfile CMD
- [ ] T3 [P] Create `evals/cases.json` and `scripts/run_evals.py`
- [ ] T4 [P] Create `infra/agent_harness.tf`; OTEL env vars in `agent_runtime.tf`; `agent_runtime_arn` output
- [ ] T5 Add eval widget to `infra/observability.tf`
- [ ] T6 [P] Create `.harness/pipeline.yaml` and `docs/harness-delegate-policy.md`
- [ ] T7 Document Transaction Search enable step and eval run in `deploy.ps1`/docs
- [ ] T8 Run fmt, validate, tflint, checkov, pytest
- [ ] T9 Review `terraform plan` with user (no apply)
