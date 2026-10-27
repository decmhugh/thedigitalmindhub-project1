# Tasks: MCP image reading tool

- [x] T1 Write failing tests: MCP tools/list shows `read_image`; valid call forwards image payload; invalid image/mediaType/size/prompt rejected without runtime call; errors/blocks/logging (AC1-4, AC7-8) in `tests/test_agent_mcp.py`.
- [x] T2 Write failing tests for agent `handle_request` image message building and vision-agent selection in `tests/test_agent_graph.py` (AC2, AC5, AC6).
- [x] T3 Implement `read_image` in `src/agent_mcp/handler.py` (AC1-4, AC7-9).
- [x] T4 Implement image support in `agent/graph.py` and vision model/agent selection in `agent/app.py` (AC2, AC5, AC6).
- [x] T5 Update `infra/agent_runtime.tf`: vision model local, IAM ARNs, `VISION_MODEL_ID` env (AC5).
- [x] T6 Run pytest, terraform fmt/validate, tflint, checkov; show plan, do not apply.
- [ ] T7 After explicit apply approval and runtime redeploy, smoke-test `read_image` via an MCP client (AC1-9).
