# Spec: Frontend image upload

Status: Draft

## Problem / Why

Signed-in users of the browser agent page can only send text. The agent now supports image questions (spec 005, vision model) but only through MCP. Users need to attach an image in the chat UI.

## User stories

- As a signed-in user, I want to attach an image to my message so the agent can describe or answer questions about it.
- As a user, I want to preview and remove the attached image before sending.
- As an operator, I want image type and size limits enforced client- and server-side, and no image data logged.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/invoke | Existing body plus optional `image`: `{"mediaType": "image/png" \| "image/jpeg" \| "image/gif" \| "image/webp", "data": <base64>}`. `prompt` stays required unless an image is present (then defaults to "Describe this image.") | `200 {"response": str}` (unchanged) | `400` invalid/oversized/unsupported image; existing 401/422/502/504 unchanged |

Requests without `image` behave exactly as today. The page keeps the same sign-in cookie and session handling; image and text turns share the conversation session.

## Acceptance criteria

- AC1: The composer has an attach-image control accepting png, jpeg, gif, webp; selecting a file shows a thumbnail preview with a remove control.
- AC2: Files with unsupported type or over 3.75 MB are rejected in the browser with a clear message and not sent.
- AC3: Sending with an image posts it to `/agent/invoke`; the user message shows the image thumbnail, and the agent reply is shown as usual. The attachment is cleared after send.
- AC4: An image alone (empty text) is allowed and uses the default prompt.
- AC5: The server rejects an `image` with a bad media type, non-base64 data, empty data, non-object shape, or decoded size over 3.75 MB with `400` and does not invoke the agent runtime.
- AC6: Valid images are forwarded to the agent runtime as `{"prompt", "image"}` (same payload as spec 005).
- AC7: Text-only requests and all existing statuses (401, 422, 502, 504) are unchanged.
- AC8: Logs contain no image data or prompts.
- AC9: Unauthenticated requests with an image still return 401.

## Non-functional

- Auth: existing SMS sign-in cookie; rate limit unchanged (2 rps, burst 5).
- Request body limit raised to ~5.1 MB for `/agent/invoke` (API Gateway HTTP API/Lambda payload ceilings are 10 MB/6 MB).
- Images processed in memory only; never stored by this endpoint.
- No new third-party frontend libraries (jQuery already present).

## Out of scope

- Multiple images, drag-and-drop/paste, image URLs, S3 storage, image compression/resizing.
- Changes to the MCP endpoint or auth flow.

## Open questions

- None.
