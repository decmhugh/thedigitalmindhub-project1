# Plan: <feature>

Spec: ./spec.md

## Design

- Lambda(s): `src/<endpoint>/handler.py`, route key, runtime
- Environment variables:
- Data stores / AWS services:

## IAM (least privilege)

| Action | Resource | Justification |
| ------ | -------- | ------------- |

## Terraform changes

## Constitution check

- [ ] One Lambda per endpoint
- [ ] Least-privilege IAM
- [ ] No secrets in repo
- [ ] Log group with retention

## Risks / alternatives
