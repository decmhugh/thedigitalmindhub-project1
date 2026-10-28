# Spec: S3 presigned uploads for images and documents

Status: Draft

Supersedes the base64 `image` field on `/agent/invoke` from spec 006 for the browser. The base64 `read_image` MCP tool from spec 005 stays for backward compatibility.

## Problem / Why

Base64 in JSON bodies caps files at a few MB, inflates payloads, and runs through the API Gateway and Lambda. Users want to upload images and documents directly to S3 with a short-lived URL, and have the agent's vision-capable model read them.

## User stories

- As a signed-in user, I want to attach an image or document in the chat and have it uploaded straight to S3 so large files work reliably.
- As a user, I want the agent to answer questions about the uploaded file in my conversation.
- As an MCP client user, I want to upload a file with a temporary URL and then ask the agent about it.
- As an operator, I want uploads private, size/type-limited, scoped per user/session, and auto-deleted.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /agent/upload-url | `{"mediaType": str, "size": int}` (signed-in cookie required) | `200 {"url": str, "fields": {...}, "key": str, "expiresIn": 300}` presigned S3 POST for one object | `400` unsupported type or size over 25 MB; `401` not signed in |
| POST | /agent/invoke | Existing body plus optional `file`: `{"key": str}` (from upload-url). `image` base64 field is no longer accepted. `prompt` optional when `file` present (default "Describe this file.") | `200 {"response": str}` | `400` invalid/unknown/foreign key, missing/unsupported object; `413 {"error": "File exceeds the model size limit"}` when the file is within the 25 MB upload cap but over the model limit; `401`, `422`, `502`, `504` unchanged |
| MCP tool | /agent/mcp `get_upload_url` | `{"mediaType": str, "size": int}` | Same structure as above (key scoped to the MCP session) | `-32602` invalid input |
| MCP tool | /agent/mcp `read_file` | `{"key": str, "prompt"?: str}` | `{"response": str, "blocked": bool}` | `-32602` invalid/unknown/foreign key; tool error "File exceeds the model size limit" when over the model limit |

Supported types: `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `application/pdf`, `text/plain`, `text/csv`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (docx).

Flow: browser/client requests an upload URL, POSTs the file directly to S3, then calls invoke/`read_file` with the `key`. The Lambda reads the object from S3 and passes its bytes to the agent runtime; the agent uses a vision/document-capable Nova model.

## Acceptance criteria

- AC1: `/agent/upload-url` requires sign-in, validates type and size, and returns a presigned S3 POST valid for 5 minutes that only permits that content type and a size up to the limit.
- AC2: Objects are stored under a key prefix bound to the caller (hash of the signed-in user, or the MCP session); invoke/`read_file` reject keys outside the caller's prefix.
- AC3: The browser uploads directly to S3 (no file bytes through API Gateway), shows progress/preview, and then sends the `key` to `/agent/invoke`.
- AC4: Invoke validates the object exists and its type is allowed; failures return `400` without invoking the runtime.
- AC4a: Files up to 25 MB are accepted for upload. If the stored size exceeds the model limit (images 3.75 MB, documents 4.5 MB), invoke/`read_file` returns the model-limit error (`413` / MCP tool error) without invoking the runtime. If the model or runtime still reports a size/input-limit error, it is mapped to the same model-limit error rather than a generic `502`.
- AC4b: The browser shows a clear "file is too large for the model" message for the model-limit error, and still allows the user to continue the conversation.
- AC5: Valid files reach the agent and are answered using the vision/document model; text-only requests are unchanged.
- AC6: File turns share the conversation session, as in specs 005/006.
- AC7: MCP clients can call `get_upload_url`, upload, then `read_file`; MCP keys are scoped to the MCP session and cannot read other sessions' or the browser's files.
- AC8: The bucket is private (block public access, encryption at rest, TLS-only), has CORS for the site origin only, and a lifecycle rule deleting objects after 7 days.
- AC9: Logs contain no file contents, filenames, prompts, or raw session/user identifiers.
- AC10: Existing rate limits, sign-in requirements, and error codes are preserved; the new endpoint uses the same throttle (2 rps, burst 5).

## Non-functional

- Size limits: upload cap 25 MB. The Converse API limits images to 3.75 MB and documents to 4.5 MB per file, so larger files are stored but rejected at invoke time with the model-limit error (AC4a). The limits are configuration constants.
- Presigned URL expiry 5 minutes; keys are random and unguessable; filenames are never used in keys.
- Anonymous MCP uploads are abuse-prone; limited by throttle, per-type size caps, and the 7-day lifecycle.
- Files processed by Lambda in memory only.

## Out of scope

- Multiple files per message, resumable/multipart upload, virus scanning, OCR pipelines, file download links, deleting files on demand.
- Changing auth or the sign-in flow.

## Open questions

- None. Resolved: accept uploads up to 25 MB and return a model-limit error when a file exceeds what the model accepts.
