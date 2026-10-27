# Tasks: Public MCP interface for the AI agent

- [ ] T1 Write failing unit tests for MCP initialization, `ask_agent` discovery and calls, invalid prompts, guardrail/runtime errors, session isolation, renewal, expiry, and deletion (AC1-AC10).
- [ ] T2 Implement `src/agent_mcp/handler.py` with the Streamable HTTP MCP protocol surface and mocked AgentCore/session-store boundaries (AC1-6, AC9-10).
- [ ] T3 Add the DynamoDB session table with 24-hour inactivity expiry and least-privilege endpoint IAM; add the Lambda endpoint and `ANY /agent/mcp` route (AC3, AC7, AC9-10).
- [ ] T4 Configure the MCP route throttle and verify the existing `/agent/invoke` route and frontend behavior remain unchanged (AC7).
- [ ] T5 Run focused MCP tests and the project pytest suite; run Terraform fmt, validate, tflint, and checkov.
- [ ] T6 Review the Terraform plan with the user; do not apply without explicit approval.
- [ ] T7 After explicit apply approval, smoke-test initialization, discovery, session continuity/isolation, tool calls, and deletion using a Streamable HTTP MCP client (AC1-10).
