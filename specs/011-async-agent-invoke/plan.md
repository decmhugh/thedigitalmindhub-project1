# Plan: Asynchronous agent invocation

Spec: ./spec.md

## Design

- `src/agent_invoke/handler.py` (existing, `POST /agent/invoke`): keeps auth, body and file-key validation. It no longer reads S3 or calls the runtime. It creates a job, starts the worker with `lambda:InvokeFunction` (`InvocationType=Event`), returns `202 {"job_id"}`. Timeout drops to 10 s.
- `src/agent_invoke_worker/handler.py` (new, no API route): receives `{job_id, user, session, prompt, file_key, new_session}`, runs the current flow (`_load_file`, `InvokeAgentRuntime`, error mapping) and writes `done` or `failed`. Timeout 300 s, memory 256 MB. Shared logic (file loading, error mapping, session id) moves into the worker; the API handler keeps only validation.
- `src/agent_job_status/handler.py` (new, `GET /agent/jobs/{job_id}`): validates the auth cookie, reads the job, returns the state only if `owner` matches the caller's `phone_hash`; otherwise `404`.
- Job id: `uuid4().hex` (128-bit). Owner stored as `phone_hash`.
- One running job per session: conditional `PutItem` on a `SESSION#<hash>` lock item (`attribute_not_exists` or expired), cleared by the worker on finish. Lock TTL 6 minutes so a crashed worker cannot block a user. Second POST gets `409`.
- Frontend `agent.html`: on `202`, poll `GET /agent/jobs/{id}` every 2 s (back-off to 5 s, max 5 minutes), show "working", then render exactly as today. `409` shows "Still working on your last request".
- Environment variables:
  - API handler: `AUTH_TABLE`, `JOBS_TABLE`, `WORKER_FUNCTION_NAME`, `LOG_LEVEL`
  - Worker: `AGENT_RUNTIME_ARN_PARAM`, `UPLOAD_BUCKET`, `JOBS_TABLE`, `LOG_LEVEL`
  - Status: `AUTH_TABLE`, `JOBS_TABLE`, `LOG_LEVEL`
- Data store: DynamoDB `${prefix}-agent-jobs` (on-demand, PK `pk`, TTL attribute `expires_at`, 1 hour for jobs, 6 minutes for locks, SSE on, point-in-time recovery off).

## IAM (least privilege)

| Role | Action | Resource | Justification |
| ---- | ------ | -------- | ------------- |
| agent-invoke | `lambda:InvokeFunction` | worker function ARN | Start the job |
| agent-invoke | `dynamodb:PutItem` | jobs table | Create job and session lock |
| agent-invoke | `dynamodb:GetItem` | auth table | Existing session check |
| worker | `bedrock-agentcore:InvokeAgentRuntime` | runtime ARN and `/runtime-endpoint/*` | Existing |
| worker | `ssm:GetParameter` | runtime ARN parameter | Existing |
| worker | `s3:GetObject` | uploads bucket `u/*` | Existing |
| worker | `dynamodb:UpdateItem`, `dynamodb:DeleteItem` | jobs table | Write result, clear lock |
| agent-job-status | `dynamodb:GetItem` | jobs table and auth table | Read job, check caller |

The invoke role loses `InvokeAgentRuntime`, `ssm:GetParameter` and `s3:GetObject`.

## Terraform changes

- New `infra/agent_jobs.tf`: jobs table, worker policy and module, status policy and module.
- `infra/modules/lambda_endpoint`: make the API route optional (`route_key = null` skips integration, route and API Gateway permission) so the worker has no public route. No reserved concurrency is set.
- `infra/agent_invoke.tf`: replace the policy and environment, set `timeout = 10`.
- `infra/main.tf`: add `GET /agent/jobs/{job_id}` throttling (2 rps burst 5 is too low for polling, so use 10 rps burst 20) and add the status module to `depends_on`.
- `infra/observability.tf`: add worker and status log groups to existing widgets if present.
- `deploy.ps1`: no change expected.
- Docs and diagrams: update `docs/harness.md` flow notes and `scripts/gen_diagrams.py` sequence (202, worker, poll) so the generated sequence reflects the new flow.

## Constitution check

- [x] One Lambda per endpoint (invoke, worker, status)
- [x] Least-privilege IAM (table above; no `*` actions)
- [x] No secrets in repo
- [x] Log group with retention (module default 14 days)
- [x] Plan reviewed by human; `apply` needs explicit approval

## Risks / alternatives

- Polling traffic raises API calls; mitigated by back-off and a higher throttle for the status route.
- A worker crash leaves a `running` job until the lock TTL; the status endpoint reports `failed` if `running` is older than 6 minutes.
- Async Lambda invoke retries twice on error by default; the worker sets `MaximumRetryAttempts = 0` through `aws_lambda_function_event_invoke_config` to avoid duplicate agent calls.
- Alternative (rejected by user): Lambda Function URL with response streaming.
- Existing tests for `agent_invoke` move to the worker for the runtime and file cases; validation and 202 tests stay with the API handler.
