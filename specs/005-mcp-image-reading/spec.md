# Spec: MCP image reading tool

Status: Draft

## Problem / Why

The MCP server only exposes text prompts. MCP clients cannot ask the agent about an image (screenshot, chart, document photo). A vision-capable model is needed alongside the existing text model.

## User stories

- As an MCP client user, I want to send an image with a question so the agent can describe or extract information from it.
- As an MCP client user, I want image questions to share my MCP session's conversation context.
- As an operator, I want image size/type limits and no logging of image data, prompts, or responses.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/mcp | MCP `tools/list` and `tools/call` for new tool `read_image` with `{"image": str (base64), "mediaType": "image/png" \| "image/jpeg" \| "image/gif" \| "image/webp", "prompt": str (optional, default "Describe this image.")}` | `{"response": str, "blocked": bool}` (same shape as `ask_agent`) | `-32602` for invalid/oversized/unsupported image; tool error for runtime failures or guardrail blocks |

`ask_agent` and the other `/agent/mcp` methods are unchanged. The tool is routed through the existing AgentCore agent runtime; the runtime uses an Amazon Nova vision-capable model (Nova Lite by default, configurable) for image requests, while text-only requests keep using the current model.

## Acceptance criteria

- AC1: `tools/list` returns both `ask_agent` and `read_image` with input/output schemas.
- AC2: A valid `read_image` call returns the agent's text answer about the image via the existing agent runtime.
- AC3: Missing/non-base64 image, unsupported `mediaType`, or decoded size above 3.75 MB is rejected with `-32602` and does not invoke the runtime.
- AC4: An empty or omitted prompt falls back to the default prompt; a non-string prompt is `-32602`.
- AC5: Image requests use the configured vision model; text-only `ask_agent` behavior and model are unchanged.
- AC6: Image calls share the MCP session's conversation context with `ask_agent` calls; sessions remain isolated.
- AC7: Guardrail blocks and runtime failures behave like `ask_agent` (blocked indicator / generic errors, no internal details).
- AC8: Logs contain no image data, prompts, responses, or raw session IDs.
- AC9: Existing rate limit (2 rps, burst 5), anonymous access, and 24h session expiry still apply.

## Non-functional

- Image payload cap 3.75 MB decoded (~5 MB base64) to fit API Gateway/Lambda 6 MB-class limits and Bedrock image limits.
- Vision model must be enabled in the account/region; model ID supplied via configuration.
- Images are processed in-memory only; not stored.

## Out of scope

- Image URL fetching, S3 upload, image generation, multiple images per call, video/PDF.
- Changes to `/agent/invoke` and the browser frontend.
- Authentication changes.

## Open questions

- None. (Assumes `eu.amazon.nova-lite-v1:0` as default, matching the existing `eu.` Nova Pro inference profile.)
