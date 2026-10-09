# Spec: AI agent core (Amazon Bedrock AgentCore)

Status: Draft

## Problem / Why

Expose an AI agent hosted on Amazon Bedrock AgentCore Runtime through the existing HTTP API, so clients can chat with it without AWS credentials.

## User stories

- As a client app, I want to send a prompt and get the agent's reply so that I can embed the agent in my product.
- As a client app, I want to reuse a session id so that the agent keeps conversation context.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/invoke | JSON `{"prompt": str, "session_id": str (optional, 33+ chars)}` | 200 `{"response": str, "session_id": str}` | 400 invalid/missing prompt; 502 agent runtime failure; 504 agent timeout |

## Acceptance criteria

- AC1: A valid request invokes the AgentCore runtime (`InvokeAgentRuntime`) with the prompt as payload and returns 200 with the agent's text and the session id.
- AC2: A missing or empty `prompt`, or a body that is not valid JSON, returns 400 without calling the runtime.
- AC3: If `session_id` is omitted, one is generated and returned; if supplied, it is passed through unchanged.
- AC4: Runtime errors map to 502 (service error) or 504 (timeout) with a generic message; no internal details leak.
- AC5: The agent runtime ARN is read from configuration (SSM parameter name in env), never hardcoded.

## Non-functional

- Handler timeout 60 s, memory 256 MB.
- Prompts are not logged; log only session id, status, and latency.
- Authentication: none for now (see open questions).

## Out of scope

- Building or deploying the agent runtime itself (container/agent code).
- Streaming responses.
- Authentication and rate limiting.

## Open questions

- [NEEDS CLARIFICATION] Is the AgentCore runtime already deployed? If not, should a separate spec cover it?
- [NEEDS CLARIFICATION] Which auth should protect `/agent/invoke` (IAM, JWT authorizer, API key)?
- [NEEDS CLARIFICATION] Is streaming needed in a later iteration?
