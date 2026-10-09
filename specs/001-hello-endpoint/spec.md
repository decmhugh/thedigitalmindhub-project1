# Spec: Hello endpoint

Status: Approved

## Problem / Why

Smoke-test endpoint proving the deploy pipeline works.

## User stories

- As an operator, I want a health-style endpoint so that I can verify the API is live.

## API contract

| Method | Path | Request | Success | Errors |
| ------ | ---- | ------- | ------- | ------ |
| GET | /hello | none | 200 `{"message": "hello"}` | none |

## Acceptance criteria

- AC1: `GET /hello` returns status 200 and JSON body `{"message": "hello"}`.

## Out of scope

Authentication.
