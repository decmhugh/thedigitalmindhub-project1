# Spec: Asynchronous agent invocation (beat the 30 s API limit)

Status: Approved

## Problem / Why

API Gateway HTTP APIs cut a request off at 30 s. `/agent/invoke` runs the agent synchronously (Lambda timeout 60 s), so slow requests (large PDFs, images, tool loops such as Code Interpreter or Browser) return `{"message":"Service Unavailable"}` while the Lambda keeps running and bills for the work. Observed: an image request ran 48.8 s. Users see a generic failure and the result is lost.

## User stories

- As a user, I want long agent requests to finish and show their answer so that large files and tool use do not fail with "Service Unavailable".
- As a user, I want to see progress ("working...") so that I know the request is not stuck.
- As an operator, I want one log line per job with a status and duration so that I can diagnose slow or failed jobs.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | `/agent/invoke` | unchanged body (`prompt`, `file`, `new_session`) | `202 {"job_id"}` | unchanged validation errors (400, 401, 413); `429` throttle |
| GET | `/agent/jobs/{job_id}` | none (auth cookie) | `200 {"status": "pending"\|"running"\|"done"\|"failed", "response"?, "blocked"?, "error"?}` | `401` not signed in; `404` unknown job or job owned by another user |

The final `done` payload equals today's `200` body of `/agent/invoke`. Failed jobs carry the same generic messages and status mapping as today (422 blocked → `done` with `blocked: true`; 413 and 502/504 → `failed` with `error` and `http_status`).

## Acceptance criteria

- AC1: `POST /agent/invoke` validates auth, body and file (as today), stores a job, starts the work asynchronously, and returns `202` with an unguessable `job_id` in under 3 s.
- AC2: A worker (Lambda, separate from the API-facing handler) runs the existing flow (S3 read, `InvokeAgentRuntime`, error mapping) and writes `done` or `failed` to the job record. Its timeout is at least 5 minutes.
- AC3: `GET /agent/jobs/{job_id}` returns the job state only to the user who created it; other users and unknown ids get `404`.
- AC4: Job records expire automatically (TTL 1 hour). Records hold the response text, no file bytes, no prompts in logs.
- AC5: The page polls the job (every 2 s, back-off to 5 s, max 5 minutes), shows a "working" indicator, then renders the response; blocked and failed results render as they do today.
- AC6: MCP `ask_agent`, `read_image` and `read_file` keep working unchanged (MCP is not behind the 30 s limit concern in this spec).
- AC7: Existing auth, rate limits (2 rps, burst 5) and guardrail behaviour are preserved. A user may have at most one running job per session; a second POST returns `409`.
- AC8: Logs are structured JSON with `job` (hashed), `status`, `latency_ms`, `error_type`, `http_status`, `request_id`. No prompts, filenames or raw identifiers.
- AC9: Each Lambda has its own least-privilege IAM role; log groups have retention set; all resources are in Terraform.

## Non-functional

- Auth: same `auth_session` cookie. Job ids are 128-bit random and are also bound to the owner (`phone_hash`).
- Latency: POST under 3 s; GET under 500 ms.
- Cost: one extra DynamoDB table (on-demand, TTL) and one worker Lambda. Abuse limits as in spec 004.
- Data sensitivity: responses are stored for at most 1 hour.

## Out of scope

- Streaming tokens to the browser.
- Changing the MCP endpoint's transport.
- Cancelling a running job.

## Decisions

- Mechanism: async Lambda invoke from the API handler plus DynamoDB polling.
- Worker: a separate Lambda (not exposed via API Gateway).
- Fast requests: `POST /agent/invoke` always returns `202`; clients poll.
