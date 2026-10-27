# Plan: MCP image reading tool

Spec: ./spec.md

## Design

- `src/agent_mcp/handler.py`: add `read_image` to `tools/list` and `tools/call`. Validate `image` (strict base64, decoded <= 3.75 MB), `mediaType` allowlist, and optional string `prompt` (default "Describe this image."). Invoke the same AgentCore runtime/session with payload `{"prompt": ..., "image": {"mediaType": ..., "data": <base64>}}`. Reuse the existing response/guardrail/error handling; nothing about the image is logged.
- `agent/graph.py`: `handle_request` accepts an optional `image` and builds a multimodal user message (text + `image_url` data-URL block). Image payloads are validated again (media type, non-empty) and selected via a `vision` flag.
- `agent/app.py`: `get_agent(vision=False)` caches a second agent built with the vision model (`VISION_MODEL_ID`, default `eu.amazon.nova-lite-v1:0`) using the same tools and the same AgentCore memory checkpointer, so image and text turns share the session thread (AC6). Text requests keep using `MODEL_ID`.
- Terraform (`infra/agent_runtime.tf`): add `local.agent_vision_model_id = "amazon.nova-lite-v1:0"`, add its inference-profile and foundation-model ARNs to the runtime role's Bedrock invoke policy, and set `VISION_MODEL_ID` env var. Lambda IAM, route, and throttle are unchanged. The runtime container must be rebuilt/redeployed.

## IAM (least privilege)

| Action | Resource | Justification |
| ------ | -------- | ------------- |
| Existing Bedrock invoke actions on runtime role | Add Nova Lite inference profile + foundation model ARNs only | Vision model invocation |

No new Lambda permissions.

## Constitution check

- [x] One Lambda per endpoint (no new Lambda)
- [x] Least-privilege IAM (specific model ARNs)
- [x] No secrets in repo
- [x] Log group with retention (unchanged)
- [x] Terraform-managed
- [x] No apply without explicit approval

## Risks / alternatives

- Lambda/API Gateway 6 MB / 10 MB payload ceilings: cap enforced at 3.75 MB decoded (base64 inflation ~4/3).
- AgentCore runtime payload size limits may be tighter; verify in smoke test.
- Image bytes persisted in AgentCore memory checkpoints via the shared thread; acceptable for demo, noted as cost/size risk.
- Nova Lite must be enabled in the account/region.
- Alternative (rejected): direct Bedrock call from Lambda, which would bypass guardrails/session memory.
