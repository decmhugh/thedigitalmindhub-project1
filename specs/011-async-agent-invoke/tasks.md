# Tasks: Asynchronous agent invocation

- [ ] T1 Write failing tests for the API handler (`tests/test_agent_invoke.py`): 202 with `job_id`, worker invoked async with the right payload, validation/401/413-key errors unchanged, `409` while a job runs, no prompts or filenames in logs (AC1, AC7, AC8)
- [ ] T2 Write failing tests for `src/agent_invoke_worker` (`tests/test_agent_invoke_worker.py`): done, blocked, 413, 502/504 mapping, file load, lock cleared in all cases, structured log fields (AC2, AC8)
- [ ] T3 Write failing tests for `src/agent_job_status` (`tests/test_agent_job_status.py`): 401, 404 for unknown and foreign jobs, pending/running/done/failed payloads, stale `running` reported as `failed` (AC3, AC4)
- [ ] T4 Implement `src/agent_invoke_worker/handler.py` (move flow out of `agent_invoke`)
- [ ] T5 Slim down `src/agent_invoke/handler.py` to validate, lock, create job, start worker, return 202
- [ ] T6 [P] Implement `src/agent_job_status/handler.py`
- [ ] T7 [P] Make the API route optional in `infra/modules/lambda_endpoint`
- [ ] T8 [P] Add `infra/agent_jobs.tf` (table with TTL, worker and status modules, IAM, event invoke config with no retries) and update `infra/agent_invoke.tf`, `infra/main.tf` throttling and dependencies
- [ ] T9 Update `src/agent_frontend/agent.html` to poll jobs (2 s to 5 s back-off, 5 minute cap, working indicator, `409` message) with a test in the existing frontend test file (AC5)
- [ ] T10 Update `scripts/gen_diagrams.py` sequence and `tests/test_diagrams.py`; update `docs/harness.md` and specs 002/004 notes where the invoke contract is described
- [ ] T11 Run fmt, validate, tflint, checkov, pytest
- [ ] T12 Review `terraform plan` with user; apply only with explicit approval; deploy with `./deploy.ps1 -BuildImage`
