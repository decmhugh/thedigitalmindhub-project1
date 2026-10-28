# Tasks: S3 presigned uploads for images and documents

- [x] T1 Write failing tests for `src/agent_upload_url`: 401 without sign-in, type/size validation (>25 MB, bad type), presigned POST conditions and 300 s expiry, caller-scoped key, no filenames/identifiers in logs (AC1, AC2, AC9, AC10).
- [x] T2 Write failing tests in `tests/test_agent_invoke.py`: `file.key` flow (Head/Get, forwarded payload, default prompt), foreign/missing key `400`, unsupported type `400`, over-model-limit `413` without runtime call, runtime size error mapped to `413`, base64 `image` field removed (AC2, AC4, AC4a, AC5, AC6, AC9).
- [x] T3 [P] Write failing tests in `tests/test_agent_mcp.py` for `get_upload_url` and `read_file` (session-scoped keys, other-session key rejected, model-limit tool error, tools listed) (AC7, AC4a).
- [x] T4 [P] Write failing tests in `tests/test_agent_graph.py` for `file` payloads (image, pdf/txt/csv/docx document blocks, invalid mime/empty data, vision agent chosen) (AC5, AC6).
- [x] T5 [P] Update `tests/test_agent_frontend_page.py` for the upload-url + S3 POST flow, accepted types, 25 MB cap, `413` message, no base64 image field (AC3, AC4b).
- [x] T6 Implement `src/agent_upload_url/handler.py` (AC1, AC2, AC9).
- [x] T7 Implement file handling and `413` mapping in `src/agent_invoke/handler.py`; remove base64 `image` input (AC2, AC4, AC4a, AC5, AC6).
- [x] T8 Implement `get_upload_url` and `read_file` in `src/agent_mcp/handler.py` (AC7).
- [x] T9 Implement `file` support in `agent/graph.py` and `agent/app.py`; verify block format with installed `langchain_aws` (AC5, AC6).
- [x] T10 Implement the browser upload flow in `src/agent_frontend/agent.html` (AC3, AC4b).
- [x] T11 Add `infra/agent_uploads.tf` (bucket, encryption, public access block, TLS-only policy, CORS, 7-day lifecycle, upload-url Lambda and IAM), route throttle in `infra/main.tf`, and `UPLOAD_BUCKET` env/IAM for invoke and MCP Lambdas (AC8, AC10).
- [x] T12 Run pytest (excluding the pre-existing IASA collection failures), `terraform fmt`, `terraform validate`; run tflint/checkov if installed.
- [ ] T13 Generate the Terraform plan with AWS credentials and review it with the user; do not apply.
- [ ] T14 After explicit apply approval and runtime redeploy, smoke-test browser and MCP uploads (image, PDF, docx, over-model-limit file) (AC1-10).
