# Tasks: SMS login and auth endpoints

- [ ] T0 Review and approve spec, plan, and tasks (also update 002 plan/tasks "anonymous" wording)
- [ ] T1 Write failing tests `tests/test_auth_start.py` (AC1, AC2, AC9, AC10)
- [ ] T2 [P] Write failing tests `tests/test_auth_verify.py` (AC3, AC4, AC10)
- [ ] T3 [P] Write failing tests `tests/test_auth_session.py` and `tests/test_auth_logout.py` (AC5, AC6)
- [ ] T4 [P] Extend `tests/test_agent_invoke.py` for 401 without session and user-bound runtime session (AC7)
- [ ] T5 [P] Update `tests/test_agent_frontend_page.py` for the SMS flow, 401 and 429 handling, no storage (AC8)
- [ ] T6 Implement `src/auth_start/handler.py` (AC1, AC2, AC10)
- [ ] T7 Implement `src/auth_verify/handler.py` (AC3, AC4)
- [ ] T8 [P] Implement `src/auth_session/handler.py` (AC5)
- [ ] T9 [P] Implement `src/auth_logout/handler.py` (AC6)
- [ ] T10 Add session check and user-bound runtime session to `src/agent_invoke/handler.py` (AC7)
- [ ] T11 Add `infra/auth.tf`: DynamoDB table with TTL/PITR, SSM pepper, `random` provider, four Lambda modules with least-privilege IAM (AC1-AC6)
- [ ] T12 Update `infra/main.tf` throttles and `infra/agent_invoke.tf` env/IAM (AC7, AC9)
- [ ] T13 Update `src/agent_frontend/agent.html` with phone/code/chat flow and sign-out (AC8)
- [ ] T14 Run pytest, Terraform fmt/validate, tflint, checkov
- [ ] T15 Show `terraform plan`; do not apply
- [ ] T16 After approved apply, run `deploy.ps1` to publish the page and smoke-test sign-in end to end
