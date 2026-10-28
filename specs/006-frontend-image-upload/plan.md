# Plan: Frontend image upload

Spec: ./spec.md

## Design

- `src/agent_invoke/handler.py`: parse optional `image` from the body. Validate with the same rules as `src/agent_mcp/handler.py` (media-type allowlist, strict base64, non-empty, decoded <= 3.75 MB); `400` on failure before any runtime call. If an image is present and `prompt` is empty/missing, use "Describe this image."; without an image the prompt stays required. Forward `{"prompt", "image"}` to the runtime. Raise `MAX_BODY` handling if any exists (body is parsed from the event; no size cap today). Order stays: auth (401) first, then validation.
- Each Lambda is self-contained (no shared layer), so the small validator is duplicated rather than adding a shared module.
- `src/agent_frontend/agent.html`: add an attach button + hidden `<input type="file" accept="image/png,image/jpeg,image/gif,image/webp">` in the composer, a preview thumbnail with remove button, client-side type/size checks, and FileReader to base64. Submit allows empty text when an image is attached. User bubble shows the thumbnail (created via DOM `img` with `src` from a data URL, never via HTML string concatenation). Clear attachment after send/new session.
- No Terraform changes: the route, throttle, IAM and runtime env are unchanged (API Gateway HTTP API limit 10 MB, Lambda 6 MB, above the ~5 MB base64 cap). Check whether the frontend Lambda/page CSP (if any) allows `img-src data:`; adjust headers in `agent_frontend/handler.py` only if needed.

## IAM (least privilege)

No changes.

## Constitution check

- [x] One Lambda per endpoint (no new Lambda)
- [x] Least-privilege IAM (unchanged)
- [x] No secrets in repo
- [x] Log group with retention (unchanged)
- [x] Terraform-managed (no changes)
- [x] No apply without explicit approval

## Risks / alternatives

- Depends on spec 005's runtime changes being deployed (vision model, image payload); otherwise the agent rejects or ignores images.
- Large base64 bodies near the Lambda 6 MB limit; the 3.75 MB cap leaves headroom.
- Thumbnail rendering uses data URLs created from user-selected files only; no untrusted HTML is injected.
- Alternative (rejected): presigned S3 upload, which adds storage, IAM and lifecycle scope.
