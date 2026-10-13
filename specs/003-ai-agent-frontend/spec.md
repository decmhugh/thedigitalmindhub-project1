# Spec: Agent frontend demo

Status: Approved

## Problem / Why

Provide a browser-based demo for testing the AI agent without exposing the frontend S3 object publicly. The page is returned through API Gateway and Lambda, which reads `agent.html` from S3.

## User stories

- As a demo user, I want to open the agent page and submit prompts without creating an account.
- As a demo user, I want to see replies and continue the same conversation session.
- As an operator, I want the HTML artifact stored in a private S3 bucket and readable only by the frontend Lambda.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| GET | /app | none | 200 `text/html; charset=utf-8`, body from S3 object `html/agent.html` | 502 if object cannot be read |
| POST | /agent/invoke | JSON `{"prompt": str, "new_session": bool (optional)}`; no authentication; browser sends the `agent_session` cookie automatically | 200 JSON agent response; first/new session sets the cookie | 400 invalid prompt; 429 throttled; 422 guardrail block; 502/504 agent failure |

## Deployment and object path

- API Gateway `GET /app` invokes Lambda `project1-dev-agent-frontend`.
- Source file: `src/agent_frontend/agent.html`.
- Deployment destination prefix: `s3://dmh-app-prod1-bucket/html/`; `agent.html` is stored at `s3://dmh-app-prod1-bucket/html/agent.html`.
- The destination bucket is also the Terraform state bucket. Deployment uploads must remain under the `html/` prefix and must not sync or delete objects at the bucket root or state prefix. This feature does not modify the bucket's backend, policy, encryption, versioning, or public-access configuration.
- The Lambda reads bucket and key from `AGENT_HTML_BUCKET` and `AGENT_HTML_KEY`; configure them as `dmh-app-prod1-bucket` and `html/agent.html`.
- Its role grants `s3:GetObject` only on `arn:aws:s3:::dmh-app-prod1-bucket/html/agent.html`.

## Acceptance criteria

- AC1: `GET /app` invokes the frontend Lambda, reads `html/agent.html` from S3, and returns its contents with an HTML content type.
- AC2: The S3 bucket blocks public access; only the frontend Lambda role can read `html/agent.html`.
- AC3: The deployment script runs `aws s3 sync` after Terraform setup, uploading every `*.html` file from the frontend source directory only to the `html/` prefix; `agent.html` updates when its source changes.
- AC4: The page uses jQuery 3.7.1 from the public jQuery CDN (`https://code.jquery.com/jquery-3.7.1.min.js`).
- AC5: A demo user can submit a prompt to `POST /agent/invoke` without signing in or sending an authorization token.
- AC6: The browser sends the HTTP-only `agent_session` cookie for follow-up prompts; the page never reads or stores the cookie value. "New session" sends `new_session: true` to rotate it.
- AC7: The page has no login, password, or token handling and uses no browser storage.
- AC8: The page displays a generic rate-limit response when the API returns HTTP 429.

## Non-functional

- Serve only HTTPS through API Gateway. The existing S3 state bucket must remain private and retain existing encryption and public-access protections.
- Deployment requires the AWS CLI and credentials with permission to sync HTML files to the frontend bucket.
- Do not log prompts, session contents, or response contents.
- API Gateway throttles anonymous invoke requests with conservative demo defaults to limit metered agent usage.
- The page remains usable on desktop and mobile browsers.

## Dependencies

- Spec 002's public `POST /agent/invoke` route and API Gateway throttling must exist before end-to-end chat testing. The page-serving route can be tested independently.

## Out of scope

- Authentication, production identity management, user provisioning, and multi-tenant access.
- Direct public S3 website hosting or CloudFront.

## Open questions

- None. Public anonymous invocation is explicitly requested; throttling mitigates, but does not eliminate, usage cost risk.
