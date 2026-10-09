# Plan: Hello endpoint

Spec: ./spec.md

## Design

- Lambda: `src/hello/handler.py`, route `GET /hello`, python3.12
- Environment variables: `LOG_LEVEL`
- AWS services: none

## IAM (least privilege)

| Action | Resource | Justification |
| ------ | -------- | ------------- |
| logs:CreateLogStream, logs:PutLogEvents | own log group | logging only |

## Terraform changes

`module "hello"` in `infra/main.tf`.

## Constitution check

- [x] One Lambda per endpoint
- [x] Least-privilege IAM
- [x] No secrets in repo
- [x] Log group with retention
