# Spec: Allow MCP Lambda to invoke the agent runtime

Status: Approved

## Problem / Why

The public MCP endpoint can initialize sessions, but tool calls fail with an AWS `AccessDeniedException` when its Lambda tries to retrieve the configured AgentCore runtime ARN or invoke that runtime. The MCP Lambda needs the same narrowly scoped runtime access already granted to the asynchronous agent worker.

## User stories

- As an MCP client user, I want agent tool calls to reach the configured AgentCore runtime so that the MCP endpoint returns agent responses rather than an access-denied error.
- As an operator, I want the MCP Lambda to have only the permissions required to read its runtime ARN and invoke that runtime.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/mcp | Existing Streamable HTTP MCP JSON-RPC `tools/call` request | Existing tool response from the configured agent runtime | Existing MCP/tool error behavior for runtime failures |

## Acceptance criteria

- AC1: The MCP Lambda execution role can read only the SSM parameter containing the configured AgentCore runtime ARN.
- AC2: The MCP Lambda execution role can invoke only the configured AgentCore runtime and its runtime endpoints.
- AC3: A valid `ask_agent` call reaches the runtime when the role has the required permissions; existing authorization behavior for MCP clients is unchanged.
- AC4: The Terraform IAM policy remains least-privilege and passes formatting and validation.

## Non-functional

- All permission changes are managed through Terraform.
- Do not broaden permissions to `*` actions or resources.
- Do not add client authentication or change the public MCP access contract.

## Out of scope

- Adding authentication to the MCP endpoint.
- Changing MCP protocol behavior, agent prompts, or runtime configuration.
- Applying Terraform or changing deployed AWS resources.

## Open questions

- None.
