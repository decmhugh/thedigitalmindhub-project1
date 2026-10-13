# Spec: Fully capable AI agent (Amazon Bedrock AgentCore)

Status: Draft

## Problem / Why

Provide a capable AI agent through a public demo API so users can test it without creating an account. The agent uses sandboxed tools and safety controls while limiting anonymous invocation volume.

## User stories

- As a demo user, I want to submit a prompt and get the agent's reply without signing in.
- As a demo user, I want my browser to retain conversation context in a cookie for the current browsing session, without signing in.
- As a user, I want the agent to call project tools, execute code, and browse the web when useful.
- As an operator, I want safety filtering and operational telemetry while keeping prompt content out of logs.

## Capabilities

| Capability | Implementation | Included |
| ---------- | -------------- | -------- |
| Reasoning | LangGraph agent, Claude Sonnet via Bedrock | yes |
| Hosting | AgentCore Runtime (container, created by this repo) | yes |
| Memory | AgentCore short-term session memory; no cross-session identity memory | yes |
| Tools | AgentCore Gateway (MCP) exposing selected project Lambda endpoints | yes |
| Code execution | AgentCore Code Interpreter | yes |
| Web browsing | AgentCore Browser (unrestricted site access) | yes |
| Safety | Bedrock Guardrails (content, PII, prompt attack) on input and output | yes |
| Observability | AgentCore observability, CloudWatch logs/metrics, X-Ray traces, dashboard | yes |
| Streaming | Deferred; anonymous access stays behind the throttled HTTP API | no |
| Authentication | None on the public demo invoke API; Lambda-to-runtime access uses IAM | no user login |
| RAG / Knowledge Bases | - | no |

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/invoke | JSON `{"prompt": str, "new_session": bool (optional)}`; no authentication; browser sends `agent_session` cookie automatically | 200 `{"response": str}`; first/new session response sets an `agent_session` cookie | 400 invalid body; 422 blocked by guardrail; 429 throttled; 502 runtime failure; 504 timeout |
| AgentCore runtime endpoint | Internal invocation by Lambda using IAM | Agent payload | Runtime response | Not exposed as a public client route |

## Acceptance criteria

- AC1: A valid anonymous request returns 200 with the agent's text; it does not expose the session ID in the response body.
- AC2: A missing or empty `prompt` or invalid JSON returns 400 without invoking the runtime.
- AC3: A request without an Authorization header can invoke the API; the request is still subject to API Gateway throttling.
- AC4: On the first request, the Lambda generates an unpredictable session ID and returns it only in a cookie named `agent_session`; subsequent requests continue using that cookie.
- AC5: The `agent_session` cookie is a browser-session cookie with `Path=/agent; Secure; HttpOnly; SameSite=Strict`, and has no `Expires` or `Max-Age` attribute.
- AC6: A request with `new_session: true` creates a fresh session and rotates the `agent_session` cookie.
- AC7: The agent uses earlier turns within the cookie's session; it does not retrieve or share personal memory across sessions.
- AC8: The agent can call at least one project endpoint (`GET /hello`) as a Gateway tool and use its result.
- AC9: The agent can execute code through Code Interpreter and browse a page through Browser, each only when the model requests it.
- AC10: Input or output blocked by the guardrail returns 422 with a generic message; PII is masked in responses and logs.
- AC11: Runtime errors map to 502 and timeouts to 504 with generic messages; no internal details leak.
- AC12: API Gateway applies a rate limit of 2 requests per second and a burst limit of 5 to `POST /agent/invoke`; excess requests receive 429.
- AC13: Each turn emits a trace with a non-secret correlation value, tool calls, latency, and token counts; raw cookies/session IDs are never logged.
- AC14: Runtime ARN, memory ID, Gateway URL, and guardrail ID come from configuration, never hardcoded.

## Non-functional

- Invoke Lambda: timeout 60 s, 256 MB. Agent turn budget: 55 s.
- Prompts, responses, raw cookies, and session IDs are not logged; log a non-reversible session correlation value, status, latency, and token counts.
- Tool access is least privilege: the agent only reaches Gateway targets listed in Terraform.
- Web browsing and code execution run only in AgentCore sandboxes.
- The anonymous endpoint has route-level throttling; anonymous access can incur model and tool costs.

## Out of scope

- Cognito authentication, user provisioning, and cross-session personal memory.
- Public direct access to AgentCore Runtime or streaming responses.
- RAG / Knowledge Bases, front-end UI implementation, and multi-tenant billing.

## Implementation checks

- Verify AgentCore Runtime and Claude Sonnet inference-profile availability in `eu-west-1` before deployment.
- Browser access to any website is approved; agent instructions must treat web content as untrusted.
- AWS provider upgrade from `~> 5.0` to `~> 6.0` is approved.
