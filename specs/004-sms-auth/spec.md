# Spec: SMS login and auth endpoints

Status: Approved

## Problem / Why

The deployed frontend page requests `/auth/session` and `/auth/start`, which do not exist (404). Spec 003 made the demo anonymous; this feature replaces that with mobile-number (SMS one-time code) sign-in, so agent use is limited to verified users and cost abuse is reduced. Spec 003 (frontend) and spec 002 (invoke) are updated: anonymous access is removed, and sign-in is required.

## User stories

- As a user, I want to enter my mobile number and receive a code by SMS so that I can sign in without a password.
- As a user, I want my sign-in to persist across page loads so that I can continue chatting.
- As a user, I want to sign out.
- As an operator, I want only signed-in users to call the agent so that metered usage is attributable and limited.

## API contract

All routes are JSON over HTTPS, one Lambda per route. The session token is an HTTP-only cookie; the page never reads or stores it.

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| POST | /auth/start | `{"phone": "+<E.164>"}` | 200 `{"status": "code_sent"}`; same response whether or not the number is known | 400 invalid number; 429 throttled/too many codes; 502 SMS failure |
| POST | /auth/verify | `{"phone": str, "code": str}` | 200 `{"authenticated": true}`; sets `auth_session` cookie (Secure, HttpOnly, SameSite=Strict, Path=/) | 400 malformed; 401 wrong/expired code; 429 too many attempts |
| GET | /auth/session | none (cookie) | 200 `{"authenticated": true, "phone_masked": "+353*****1234"}` or 200 `{"authenticated": false}` when there is no valid session (a status check, not an error) | 502 session store unavailable |
| POST | /auth/logout | none (cookie) | 204; clears cookie | none (idempotent) |
| POST | /agent/invoke | unchanged (spec 002) | unchanged | adds 401 when not signed in |

## Acceptance criteria

- AC1: `POST /auth/start` with a valid E.164 number sends a one-time code by SMS and returns 200 without revealing whether the account exists.
- AC2: `POST /auth/start` rejects non-E.164 input with 400 and sends nothing.
- AC3: `POST /auth/verify` with the correct, unexpired code returns 200 and sets the `auth_session` cookie; unknown users are registered on first successful verification.
- AC4: Codes expire after 5 minutes, are single-use, and are locked after 5 failed attempts (401, then 429).
- AC5: `GET /auth/session` returns 200 with `authenticated: true` and a masked number when the cookie is valid, and 200 with `authenticated: false` otherwise (so signed-out page loads produce no browser console error); the full number is never returned.
- AC6: `POST /auth/logout` invalidates the session and clears the cookie.
- AC7: `POST /agent/invoke` returns 401 without a valid `auth_session`; the agent runtime session is bound to the authenticated user.
- AC8: The page shows a phone-number form, then a code form, then chat; it checks `/auth/session` on load, handles 401/429, and uses no browser storage and never reads the cookie.
- AC9: SMS sends are subject to the standard API Gateway route throttling; no spend cap is enforced by this feature.
- AC10: Phone numbers, codes, and tokens are never logged; logs use a short hash of the number.

## Non-functional

- HTTPS only; cookie is Secure, HttpOnly, SameSite=Strict.
- No secrets in repo; any signing keys or provider credentials in SSM/Secrets Manager.
- Session lifetime: 12 hours, no refresh.
- SMS delivery incurs per-message cost. The account is out of the End User Messaging SMS sandbox (confirmed by owner).

## Out of scope

- Passwords, email login, social login, MFA beyond the SMS code, account recovery, admin user management.
- Per-user quotas beyond API throttling.

## Open questions

- Resolved: provider is custom Lambdas using AWS End User Messaging SMS (sends the SMS; no SNS, no Cognito) and DynamoDB (OTP codes, sessions).
- Assumed (change if wrong): session lifetime 12 hours; `/agent/mcp` is unchanged (out of scope).
- Resolved: use sender ID `SERV` (AWS End User Messaging registration `registration-41cb3c27f4cb4426ad8a5bd394278c22`, type IE_SENDER_ID_REGISTRATION, status Complete, eu-west-1). It is passed as the `OriginationIdentity` of `SendTextMessage` from the Terraform variable `sms_sender_id` (default `SERV`). The registration is Ireland-specific, so other destination countries may ignore or replace the sender ID.
- Resolved: no SMS spend cap and no country restriction in this feature; cost and abuse guardrails are handled elsewhere.
