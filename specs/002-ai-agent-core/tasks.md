# Tasks: Anonymous AI agent core

- [ ] T0 Review and approve the revised spec, plan, and tasks
- [x] T1 Verify AgentCore Runtime and Nova Pro inference profile are available in `eu-west-1`
- [x] T2 Upgrade AWS provider to `~> 6.0` and validate provider schema
- [ ] T3 Write failing tests for anonymous invoke validation, sessions, guardrail mapping, and runtime failures
- [ ] T4 Update `src/agent_invoke/handler.py` to accept no JWT, generate/reuse session IDs, and pass no user identity
- [ ] T5 Add invoke Lambda timeout/memory settings and per-route API Gateway throttling (2 RPS, burst 5)
- [ ] T6 Add SSM/Guardrail resources; do not create Cognito resources
- [ ] T7 Write graph tests for session-only memory and configured tools
- [ ] T8 Implement the non-streaming LangGraph runtime container
- [ ] T9 Add ECR, session Memory, Gateway, Browser, and Code Interpreter resources
- [x] T10 ECR repository, runtime pull role, and AgentCore runtime resource scaffold exist
- [ ] T11 Wire the public invoke Lambda route to the IAM-protected AgentCore runtime
- [ ] T12 Add logs, dashboard, and error-rate alarm
- [ ] T13 Run pytest, Terraform fmt/validate, tflint, and checkov
- [ ] T14 Review the Terraform plan; wait for explicit apply approval
- [ ] T15 After approved apply, run the anonymous invoke smoke test
