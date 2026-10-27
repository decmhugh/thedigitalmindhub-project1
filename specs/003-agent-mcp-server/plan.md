# Plan: Public MCP interface for the AI agent

Spec: ./spec.md

## Design

- Add `src/agent_mcp/handler.py` as a dedicated Lambda endpoint implementing the narrowly scoped Streamable HTTP protocol surface in the spec.
- Serve the MCP protocol at `ANY /agent/mcp`. Support initialization, tool discovery, tool calls, and session deletion. Return 405 for GET because server-initiated streams are out of scope.
- Expose one tool, `ask_agent`, with a required non-empty `prompt`. Invoke the existing AgentCore Runtime using the runtime ARN from the existing SSM parameter, and preserve its guardrail result.
- Create a random MCP session ID during initialization. Store its AgentCore runtime session ID and 24-hour inactivity expiry in a dedicated DynamoDB table. Validate and renew the expiry on requests; delete the record on session termination.
- Keep session state outside the Lambda process so requests routed to different Lambda instances retain the same behavior. Do not alter the existing `/agent/invoke` contract or frontend.
- Configure the route for public anonymous access and the existing demo throttle of 2 requests per second, burst 5.
- Set the endpoint timeout to 60 seconds and memory to 256 MB, matching the agent invoke endpoint.

## IAM (least privilege)

| Action | Resource | Justification |
| ------ | -------- | ------------- |
| `bedrock-agentcore:InvokeAgentRuntime` | Existing configured runtime ARN and its runtime-endpoint resources | Invoke the deployed agent |
| `ssm:GetParameter` | Existing runtime ARN parameter | Resolve the runtime ARN |
| `dynamodb:GetItem`, `PutItem`, `UpdateItem`, `DeleteItem` | New MCP session table | Create, validate, extend, and terminate MCP sessions |
| Lambda log write actions | The MCP Lambda log group | Emit operational logs |

## Terraform changes

- Add `infra/agent_mcp.tf` with the dedicated session table, TTL attribute, Lambda IAM policy, and Lambda endpoint module.
- Add `ANY /agent/mcp` route throttling to `infra/main.tf`; leave the existing invoke route settings unchanged.
- Configure the Lambda with the existing runtime ARN SSM parameter and the new session-table name.
- Use the reusable endpoint module's explicit log group and retention. No changes to backend or Terraform state configuration.
- No third-party MCP runtime dependency is required; the protocol adapter must be covered by request/response and session lifecycle tests.

## Constitution check

- [x] One Lambda per endpoint
- [x] Least-privilege IAM
- [x] No secrets in repo
- [x] Log group with retention
- [x] Infrastructure remains Terraform-managed
- [x] Human review of plan; no apply without explicit approval

## Risks / alternatives

- Streamable HTTP requests must preserve MCP session headers through API Gateway and Lambda proxy integration; cover this in handler tests and an approved smoke test.
- The adapter implements only the specified MCP methods; unsupported methods must receive protocol-appropriate errors rather than success-shaped fallbacks.
- DynamoDB TTL deletion is asynchronous, so the handler must check the stored expiry timestamp on every request rather than rely on item removal timing.
- Public anonymous access can incur model and tool costs; reuse the demo route throttle and do not add more agent capabilities to MCP.
- Server-initiated SSE and streamed agent tokens remain unsupported; GET explicitly returns 405.
