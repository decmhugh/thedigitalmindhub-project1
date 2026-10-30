# Plan: Allow MCP Lambda to invoke the agent runtime

Spec: ./spec.md

## Design

- Lambda(s): Update the existing `agent_mcp` Lambda execution policy; no new endpoint or Lambda.
- Environment variables: No changes; continue using `AGENT_RUNTIME_ARN_PARAM`.
- Data stores / AWS services: SSM Parameter Store for the runtime ARN; Bedrock AgentCore runtime invocation.

## IAM (least privilege)

| Action | Resource | Justification |
| ------ | -------- | ------------- |
| `ssm:GetParameter` | The ARN of `local.agent_ssm_name` in the configured account and region | Read the AgentCore runtime ARN needed by the MCP Lambda. |
| `bedrock-agentcore:InvokeAgentRuntime` | `local.agent_runtime_arn` and its `/runtime-endpoint/*` resources | Invoke the configured runtime and its runtime endpoints. |

## Terraform changes

- Add the two narrowly scoped statements to `data.aws_iam_policy_document.agent_mcp` in `infra/agent_mcp.tf`.
- Follow the existing `agent_invoke_worker` IAM resource expressions in `infra/agent_jobs.tf`.
- Do not alter the route, authentication behavior, runtime handler, or other Lambda policies.

## Constitution check

- [x] One Lambda per endpoint
- [x] Least-privilege IAM
- [x] No secrets in repo
- [x] Log group with retention

## Risks / alternatives

- The reported `AccessDeniedException` does not identify whether SSM lookup or runtime invocation is denied; granting both exact permissions required by the MCP call path addresses either case.
- No client-side credential change is needed because the denied AWS calls use the MCP Lambda execution role.
