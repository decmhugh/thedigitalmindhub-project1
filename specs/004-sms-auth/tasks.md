# Tasks: SMS login and auth endpoints

- [x] T0 Review and approve spec, plan, and tasks (also update 002 plan/tasks "anonymous" wording)
- [x] T1 Write failing tests `tests/test_auth_start.py` (AC1, AC2, AC9, AC10)
- [x] T2 [P] Write failing tests `tests/test_auth_verify.py` (AC3, AC4, AC10)
- [x] T3 [P] Write failing tests `tests/test_auth_session.py` and `tests/test_auth_logout.py` (AC5, AC6)
- [x] T4 [P] Extend `tests/test_agent_invoke.py` for 401 without session and user-bound runtime session (AC7)
- [x] T5 [P] Update `tests/test_agent_frontend_page.py` for the SMS flow, 401 and 429 handling, no storage (AC8)
- [x] T6 Implement `src/auth_start/handler.py` (AC1, AC2, AC10)
- [x] T7 Implement `src/auth_verify/handler.py` (AC3, AC4)
- [x] T8 [P] Implement `src/auth_session/handler.py` (AC5)
- [x] T9 [P] Implement `src/auth_logout/handler.py` (AC6)
- [x] T10 Add session check and user-bound runtime session to `src/agent_invoke/handler.py` (AC7)
- [x] T11 Add `infra/auth.tf`: DynamoDB table with TTL/PITR, SSM pepper, `random` provider, four Lambda modules with least-privilege IAM (AC1-AC6)
- [x] T12 Update `infra/main.tf` throttles and `infra/agent_invoke.tf` env/IAM (AC7, AC9)
- [x] T13 Update `src/agent_frontend/agent.html` with phone/code/chat flow and sign-out (AC8)
- [x] T14 Run pytest, Terraform fmt/validate (tflint/checkov not installed)
- [ ] T15 Show `terraform plan`; do not apply
- [ ] T16 After approved apply, run `deploy.ps1` to publish the page and smoke-test sign-in end to end
