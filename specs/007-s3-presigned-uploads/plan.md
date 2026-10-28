# Plan: S3 presigned uploads for images and documents

Spec: ./spec.md

## Design

**Storage**
- New private S3 bucket `${prefix}-agent-uploads-<account id>`: block public access, SSE-S3 encryption, bucket policy denying non-TLS, CORS allowing `POST` from the API endpoint origin only (`aws_apigatewayv2_api.this.api_endpoint`), lifecycle rule expiring objects and aborting incomplete uploads after 7 days, versioning off, `force_destroy` true for dev.
- Keys: `u/<sha256(owner)[:32]>/<uuid4>` where owner is the signed-in `phone_hash` (browser) or `mcp:<session_id>` (MCP). The prefix hash is non-reversible; filenames never appear in keys.

**New Lambda `src/agent_upload_url/handler.py`** (`POST /agent/upload-url`, own role/log group via `lambda_endpoint`)
- Validates the sign-in cookie against the auth DynamoDB table (same logic as invoke), validates `mediaType` allowlist and `size` (1..25 MB), then `generate_presigned_post` with conditions: exact `Content-Type`, `content-length-range` 1..size, key fixed, expiry 300 s, SSE header. Returns `{url, fields, key, expiresIn}`. No file bytes pass through the API.
- IAM: `s3:PutObject` on `uploads/u/*` (presign is signed with the Lambda role, so it needs PutObject), `dynamodb:GetItem` on the auth table.

**`src/agent_invoke/handler.py`**
- Replace the base64 `image` field with `file: {"key": str}`. Require the key to start with the caller's prefix (derived from `user`); `HeadObject` for existence, `ContentType`, `ContentLength`; reject unsupported type (`400`), missing/foreign key (`400`), over model limit (`413`, images 3.75 MB, documents 4.5 MB; constants) without invoking the runtime.
- Read the object with `GetObject`, base64 the bytes, and forward to the runtime as `{"prompt", "file": {"mediaType", "data", "kind": "image"|"document", "name": "document"}}` (neutral name, never the original filename). Default prompt "Describe this file.". Map runtime/model size-limit errors (`ValidationException` mentioning size/too large/exceeds) to `413`.
- Keeps all existing 401/422/502/504 behavior. The base64 `image` input from spec 006 is removed from the API (and its tests/UI).
- IAM: add `s3:GetObject` on `u/*` and `s3:ListBucket`-free `HeadObject` (covered by `s3:GetObject`).

**`src/agent_mcp/handler.py`**
- Add tools `get_upload_url` and `read_file`. Owner is `mcp:<session_id>` so keys are session-scoped. Same presign/validate/Head/Get/limit logic. `read_image` (spec 005) stays unchanged.
- IAM: `s3:PutObject` and `s3:GetObject` on `u/*`.
- Shared logic is duplicated per Lambda (no shared layer), consistent with earlier specs.

**Agent runtime (`agent/graph.py`, `agent/app.py`)**
- `handle_request` accepts `file` (in addition to `image`): images become `{"type": "image", ...}` blocks; documents become LangChain Bedrock Converse `{"type": "file", "base64", "mime_type", "filename": "document"}` blocks (docs support pdf, txt, csv, docx). Verify block format against installed `langchain_aws`. Route to the vision/document agent (`vision=True`). Validate mime types and non-empty data.

**Frontend `agent.html`**
- Replace the base64 flow: on file select validate type and the 25 MB cap, request `/agent/upload-url`, POST FormData (fields first, `file` last) to the presigned URL with progress, then send `{file: {key}}` to `/agent/invoke`. Accept images, PDF, txt, csv, docx; show a thumbnail for images and a filename chip for documents (text only, via `.text()`). Show the "too large for the model" message on `413`; keep the conversation usable.

**Terraform**
- New `infra/agent_uploads.tf`: bucket, public access block, encryption, lifecycle, CORS, TLS-deny policy, upload-url Lambda module, `POST /agent/upload-url` route throttled 2 rps / burst 5 in `main.tf` `depends_on`.
- Add `UPLOAD_BUCKET` env var and S3 IAM statements to `agent_invoke.tf` and `agent_mcp.tf`.
- Agent runtime role needs no S3 access (Lambda passes bytes).

## IAM (least privilege)

| Principal | Action | Resource | Justification |
| --------- | ------ | -------- | ------------- |
| upload-url Lambda | `s3:PutObject` | `bucket/u/*` | Sign presigned POSTs |
| upload-url Lambda | `dynamodb:GetItem` | auth table | Verify sign-in |
| invoke Lambda | `s3:GetObject` | `bucket/u/*` | Head/read uploaded file |
| MCP Lambda | `s3:PutObject`, `s3:GetObject` | `bucket/u/*` | Presign and read MCP files |

## Constitution check

- [x] One Lambda per endpoint (new upload-url Lambda)
- [x] Least-privilege IAM (prefix-scoped, no `*` actions)
- [x] No secrets in repo
- [x] Log group with retention (module default)
- [x] Terraform-managed
- [x] No apply without explicit approval

## Risks / alternatives

- Lambda payload: invoke/MCP Lambda read up to 4.5 MB into memory and forward base64 (~6 MB) to AgentCore; the 413 check runs before reading. Increase Lambda memory if needed and verify AgentCore payload limits in the smoke test.
- Files between the model limit and 25 MB are stored but unusable; accepted per spec (7-day lifecycle).
- Anonymous MCP uploads: keys are session-scoped but sessions are anonymous; mitigated by throttle, size caps, 7-day expiry.
- Presigned POST conditions are enforced by S3, so a client cannot upload a different type or larger file than requested.
- Verify the LangChain Bedrock document block format before relying on it; fall back to Converse `document` blocks if needed.
- Alternative (rejected): agent runtime reads S3 directly, because you chose Lambda-reads-and-passes-bytes.
