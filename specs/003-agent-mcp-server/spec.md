# Spec: Public MCP interface for the AI agent

Status: Draft

## Problem / Why

MCP clients need a standard way to discover and call the deployed AI agent without using the browser-specific `/agent/invoke` contract.

## User stories

- As an MCP client user, I want to discover and call the project agent through Streamable HTTP so I can use it from MCP-compatible applications.
- As an MCP client user, I want the agent to remember earlier turns within my MCP session, without sharing conversation context with other sessions.
- As an operator, I want the endpoint to retain the demo's rate limit and avoid logging prompts, responses, or raw session identifiers.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/mcp | Streamable HTTP MCP JSON-RPC messages, including initialization, tool discovery, and tool calls | MCP protocol responses; the `ask_agent` tool accepts `{"prompt": str}` and returns the agent response, with a blocked indicator when applicable | MCP protocol errors for invalid requests; tool errors for agent/runtime failures |
| GET | /agent/mcp | Streamable HTTP server stream request | 405; server-initiated streams are not supported | MCP protocol/transport error |
| DELETE | /agent/mcp | Terminate the current MCP session | Successful session termination | MCP protocol/transport error |

The endpoint is public and anonymous, like `/agent/invoke`. MCP session identifiers are transport-managed; each session maps to isolated agent conversation context. No client-supplied identity is accepted.

## Acceptance criteria

- AC1: An MCP client can initialize a Streamable HTTP session and discover the `ask_agent` tool.
- AC2: A valid `ask_agent` call with a non-empty prompt returns the deployed agent's response using the existing agent runtime and safety behavior.
- AC3: Calls in one MCP session retain that session's conversation context; different sessions cannot read or modify one another's context.
- AC4: A missing or empty prompt is returned as an MCP tool/input error and does not invoke the agent runtime.
- AC5: Guardrail-intervened responses are represented as blocked results without exposing internal details.
- AC6: Runtime failures produce protocol-appropriate errors without leaking internal details.
- AC7: The endpoint is callable without authentication and applies the existing demo rate limit of 2 requests per second with a burst limit of 5.
- AC8: Logs contain no prompts, responses, or raw MCP/agent session identifiers; operational logs use a non-reversible correlation value.
- AC9: MCP sessions can be explicitly terminated, and termination does not affect other sessions.
- AC10: An MCP session becomes invalid after 24 hours without activity; calls after expiry must reinitialize.

## Non-functional

- MCP transport: Streamable HTTP.
- Access: public anonymous demo access, protected by API Gateway route throttling.
- Session context is isolated per MCP session; no cross-session personal memory or identity.
- Inactive MCP sessions expire after 24 hours.
- Reuse the deployed AgentCore agent and its existing guardrails and tool restrictions.
- Preserve structured logging without sensitive conversation or session data.

## Out of scope

- Local stdio transport.
- Authentication, user accounts, or cross-session memory.
- Exposing agent tools other than `ask_agent` to MCP clients.
- Replacing or changing the existing browser-facing `/agent/invoke` API.
- Server-initiated notifications or streaming agent tokens.

## Open questions

- None.
