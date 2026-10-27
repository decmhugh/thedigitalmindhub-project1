# Plan: Anonymous AI agent core

Spec: ./spec.md
Status: Draft

## Architecture

```mermaid
flowchart LR
  B[Browser] -->|anonymous HTTPS| A[HTTP API + route throttling]
  A -->|POST /agent/invoke| L[Invoke Lambda]
  L -->|IAM InvokeAgentRuntime| R[AgentCore Runtime]
  R --> M[Short-term session memory]
  S[S3 IASA source bucket] --> I[One-time ingestion]
  I --> V[S3 Vectors index]
  R -->|Titan embed + query| V
  R --> G[AgentCore Gateway]
  G --> T[Selected project Lambda tools]
  R --> CI[Code Interpreter]
  R --> W[Browser]
  R --> BR[Bedrock Nova Pro + Guardrail]
  R --> O[CloudWatch / X-Ray]
```

## Design

- Invoke Lambda validates JSON, creates or accepts a session ID, reads the runtime ARN from SSM, and invokes AgentCore using its IAM role. Payload contains only `prompt`; `runtimeSessionId` carries session context. No client identity is accepted or inferred.
- The LangGraph container uses `ChatBedrockConverse` with the `eu.amazon.nova-pro-v1:0` inference profile, Bedrock Guardrails, AgentCore Memory scoped to runtime sessions, Gateway MCP tools, Browser, Code Interpreter, and the `search_iasa_knowledge` S3 Vectors retrieval tool.
- Do not configure a public runtime endpoint. API Gateway is the only client entry point; the Lambda calls the runtime with IAM authorization.
- Keep the invoke API non-streaming. Direct runtime streaming is deferred because it would bypass API Gateway route throttling.
- Add per-route API Gateway settings for `POST /agent/invoke`: 2 requests/second, burst 5.
- Continue structured logs and traces without prompt/response contents.

## Terraform changes

- `agent_runtime.tf`: runtime, execution role, and IAM permissions.
- `agent_ecr.tf`: private ECR repository, image scanning, immutable tags, lifecycle policy.
- `agent_memory.tf`: short-term session memory only; no long-term user strategies.
- `agent_vectors.tf`: new private source bucket `project1-dev-iasa-kb-586794455900`, separate S3 Vectors bucket `project1-dev-iasa-vectors`, and 1024-dimensional `float32` cosine index. Do not reuse `dmh-kb-docs`, Aurora/RDS Knowledge Bases, or the Terraform state bucket.
- `agent/ingest_iasa.py`: one-time upload from `C:\Users\decla\OneDrive\Documents\IASA`, preserving source keys; parse Office/PDF/email/Visio/text files, use Textract for images and Transcribe for MP4 audio, chunk and embed with Titan, then upsert vectors and source metadata. Log aggregate counts only.
- `agent/tools.py`: add `search_iasa_knowledge`, which embeds each query with Titan, queries only the configured S3 Vectors index, and returns relevant chunks and source references to Nova Pro.
- `agent_gateway.tf`, `agent_tools.tf`: Gateway targets, Browser, Code Interpreter.
- `guardrail.tf`, `observability.tf`, `ssm.tf`: guardrail, logs/dashboard/alarm, and runtime configuration.
- `infra/main.tf`: add `POST /agent/invoke` Lambda module and API Gateway route throttling. Do not add a Cognito authorizer.
- `modules/lambda_endpoint`: timeout and memory-size options; preserve IAM-only runtime invocation.

## IAM

| Role | Action | Resource | Justification |
| ---- | ------ | -------- | ------------- |
| Invoke Lambda | `bedrock-agentcore:InvokeAgentRuntime` | Specific runtime ARN and endpoint | Invoke its configured runtime |
| Invoke Lambda | `ssm:GetParameter` | Runtime ARN parameter | Resolve runtime configuration |
| Agent runtime | `bedrock:InvokeModel`, `bedrock:ApplyGuardrail` | Configured model and guardrail ARNs | Reasoning and safety |
| Agent runtime | `bedrock:InvokeModel` | Titan Text Embeddings V2 model ARN | Embed incoming search queries |
| Agent runtime | `s3vectors:QueryVectors` | Exact IASA index ARN | Retrieve relevant chunks |
| Local ingestion identity | `s3:PutObject`, `s3:ListBucket` | New source bucket, `source/iasa/` prefix | Upload source documents |
| Local ingestion identity | `textract:DetectDocumentText`, `transcribe:StartTranscriptionJob`, `transcribe:GetTranscriptionJob`, `bedrock:InvokeModel`, `s3vectors:PutVectors` | New source/vector resources and configured models | Extract, embed, and index the corpus |
| Agent runtime | AgentCore memory/Gateway/tool data-plane actions | Specific memory, Gateway, Browser, and Code Interpreter resources | Session memory and selected tools |
| Agent runtime | ECR image pull actions | Specific repository; `ecr:GetAuthorizationToken` on `*` | Pull its container image |
| Gateway role | `lambda:InvokeFunction` | Each selected tool Lambda ARN | Invoke configured tool targets |

## Testing

- pytest for anonymous invoke validation, session ID behavior, runtime calls, generic errors, and 429 configuration; no live AWS calls.
- pytest for the agent graph using a fake model and fake tools; verify session-only memory behavior.
- Terraform fmt/validate/tflint/checkov; review the plan for route throttle settings and least-privilege IAM.
- Test supported parsers, chunking, Titan embedding calls, vector writes/queries, source metadata, and per-file failure reporting with mocks.
- Review the plan for new dedicated source/vector buckets only; ensure there are no changes to existing KBs, Aurora/RDS, or the state bucket.
- Manual smoke test calls `/agent/invoke` without an Authorization header and verifies a valid response.

## Constitution check

- [x] One Lambda per endpoint
- [x] Least-privilege IAM
- [x] Infrastructure as code only
- [x] Test-first
- [x] No secrets in repo
- [x] Observable, with prompt/response data excluded from logs
- [x] Human plan review; no apply without approval

## Risks

- Anonymous invocations can create model/tool costs; 2 RPS and burst 5 are demo safeguards, not strong identity-based quotas. Add a budget alarm and monitor usage.
- Textract, Transcribe, Titan embedding, and anonymous retrieval incur charges; set budget alerts and never log source document contents.
- Streaming and cross-session personal memory are not supported without an authenticated design.
- Confirm AgentCore and Nova Pro availability in `eu-west-1`; build/push ARM64 image before runtime creation.
