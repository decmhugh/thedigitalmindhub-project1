# Tasks: AI agent core

- [ ] T0 Resolve open questions in spec; get spec and plan approved
- [ ] T1 Write failing tests for AC1-AC5 (`tests/test_agent_invoke.py`)
- [ ] T2 Implement `src/agent_invoke/handler.py`
- [ ] T3 [P] Add `timeout` and `memory_size` variables to `modules/lambda_endpoint`
- [ ] T4 [P] Add SSM parameter and `module "agent_invoke"` with IAM policy in `infra/main.tf`
- [ ] T5 Verify boto3 `bedrock-agentcore` availability; package boto3 if needed
- [ ] T6 Run fmt, validate, tflint, checkov, pytest
- [ ] T7 Review `terraform plan` with user
