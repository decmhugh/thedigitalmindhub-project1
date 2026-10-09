# Plan: AI agent core

Spec: ./spec.md

## Design

- Lambda: `src/agent_invoke/handler.py`, route `POST /agent/invoke`, python3.12, timeout 60 s
- Flow: parse and validate body -> resolve runtime ARN (SSM, cached per container) -> `bedrock-agentcore` client `invoke_agent_runtime(agentRuntimeArn, runtimeSessionId, payload)` -> return text
- Environment variables: `LOG_LEVEL`, `AGENT_RUNTIME_ARN_PARAM` (SSM parameter name)
- AWS services: Bedrock AgentCore Runtime, SSM Parameter Store
- Errors: `ClientError` -> 502, read timeout -> 504

## IAM (least privilege)

| Action | Resource | Justification |
| ------ | -------- | ------------- |
| logs:CreateLogStream, logs:PutLogEvents | own log group | logging |
| bedrock-agentcore:InvokeAgentRuntime | agent runtime ARN and `<arn>/runtime-endpoint/*` | invoke only this agent |
| ssm:GetParameter | the single SSM parameter ARN | read runtime ARN |

## Terraform changes

- `aws_ssm_parameter` for the runtime ARN (value supplied outside the repo; `lifecycle.ignore_changes = [value]`).
- `module "agent_invoke"` in `infra/main.tf` with `policy_json` set to the two statements above, plus `timeout` and `memory_size` variables added to `modules/lambda_endpoint`.

## Testing

- pytest with `botocore.stub.Stubber` / mocks for AC1-AC5; no live AWS calls.

## Constitution check

- [x] One Lambda per endpoint
- [x] Least-privilege IAM
- [x] No secrets in repo
- [x] Log group with retention
- [x] Test-first

## Risks / alternatives

- Boto3 in the Lambda runtime may lack the `bedrock-agentcore` client; bundle a pinned boto3 if so.
- Alternative: Bedrock Agents `InvokeAgent`; rejected, spec targets AgentCore.
