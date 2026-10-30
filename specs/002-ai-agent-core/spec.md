# Spec: Fully capable AI agent (Amazon Bedrock AgentCore)

Status: Draft

## Problem / Why

Provide a capable AI agent through an API for signed-in users (SMS login, spec 004). The agent uses sandboxed tools and safety controls while limiting invocation volume.

## User stories

- As a signed-in user, I want to submit a prompt and get the agent's reply.
- As a signed-in user, I want my browser to retain conversation context in a cookie for the current browsing session.
- (Removed) IASA knowledge-base Q&A was dropped and never built.
- As a user, I want the agent to call project tools, execute code, and browse the web when useful.
- As an operator, I want safety filtering and operational telemetry while keeping prompt content out of logs.

## Capabilities

| Capability | Implementation | Included |
| ---------- | -------------- | -------- |
| Reasoning | LangGraph agent, Amazon Nova Pro via Bedrock | yes |
| Hosting | AgentCore Runtime (container, created by this repo) | yes |
| Memory | AgentCore short-term session memory; no cross-session identity memory | yes |
| Knowledge retrieval | Dedicated private S3 source bucket plus S3 Vectors bucket/index; Titan Text Embeddings V2; agent retrieval | yes |
| Tools | AgentCore Gateway (MCP) exposing selected project Lambda endpoints | yes |
| Code execution | AgentCore Code Interpreter | yes |
| Web browsing | AgentCore Browser (unrestricted site access) | yes |
| Safety | Bedrock Guardrails (content, PII, prompt attack) on input and output | yes |
| Observability | AgentCore observability, CloudWatch logs/metrics, X-Ray traces, dashboard | yes |
| Streaming | Deferred; access stays behind the throttled HTTP API | no |
| Authentication | SMS login per spec 004 (`auth_session` cookie) on the invoke API; Lambda-to-runtime access uses IAM | yes |
| RAG / Knowledge Bases | - | no |

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/invoke | JSON `{"prompt": str, "new_session": bool (optional)}`; requires valid `auth_session` cookie (spec 004); browser sends `agent_session` cookie automatically | 200 `{"response": str}`; first/new session response sets an `agent_session` cookie | 400 invalid body; 401 not signed in; 422 blocked by guardrail; 429 throttled; 502 runtime failure; 504 timeout |
| AgentCore runtime endpoint | Internal invocation by Lambda using IAM | Agent payload | Runtime response | Not exposed as a public client route |

## Acceptance criteria

- AC1: A valid signed-in request returns 200 with the agent's text; it does not expose the session ID in the response body.
- AC2: A missing or empty `prompt` or invalid JSON returns 400 without invoking the runtime.
- AC3: A request without a valid `auth_session` cookie returns 401 without invoking the runtime; valid requests remain subject to API Gateway throttling.
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
- AC15: (Removed) IASA knowledge base dropped; not implemented.
- AC16: (Removed) IASA knowledge base dropped; not implemented.
- AC17: (Removed) IASA knowledge base dropped; not implemented.
- AC18: (Removed) IASA knowledge base dropped; not implemented.
- AC19: (Removed) IASA knowledge base dropped; not implemented.
- AC20: (Removed) IASA knowledge base dropped; not implemented.

## Non-functional

- Invoke Lambda: timeout 10 s, 256 MB; it only queues the job. Superseded by spec 011: `POST /agent/invoke` now returns `202 {"job_id"}` (409 if a request is already running) and the result is read from `GET /agent/jobs/{job_id}`; the worker Lambda runs the agent turn (timeout 300 s).
- Prompts, responses, raw cookies, and session IDs are not logged; log a non-reversible session correlation value, status, latency, and token counts.
- Tool access is least privilege: the agent only reaches Gateway targets listed in Terraform.
- Web browsing and code execution run only in AgentCore sandboxes.
- The endpoint has route-level throttling; invocation can incur model and tool costs.
- Source objects remain private in the dedicated bucket; only the ingestion process and designated agent components receive least-privilege access.
- The corpus is approved for retrieval by signed-in users; do not upload additional private material without reviewing that exposure.

## Out of scope

- Cognito/SMS user provisioning is specified in spec 004; cross-session personal memory is out of scope.
- Public direct access to AgentCore Runtime or streaming responses.
- AWS Bedrock Knowledge Bases, Aurora/RDS vector stores, front-end UI implementation, and multi-tenant billing.

## Implementation checks

- Verify AgentCore Runtime and the Amazon Nova Pro inference profile in `eu-west-1` before deployment.
- Browser access to any website is approved; agent instructions must treat web content as untrusted.
- AWS provider upgrade from `~> 5.0` to `~> 6.0` is approved.
