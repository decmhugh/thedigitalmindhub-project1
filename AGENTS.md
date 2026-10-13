# Project Guide

Serverless AWS application managed with Terraform. One Lambda function per API endpoint behind API Gateway HTTP API. No Flask/monolithic API layer.

## Layout

- `infra/` Terraform root module (`main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, `backend.tf`)
- `infra/modules/lambda_endpoint/` Reusable module: Lambda + IAM role + log group + API route
- `src/<endpoint>/handler.py` One directory per endpoint, each with its own `handler.py`
- `tests/` Pytest unit tests
- `specs/` Spec-driven artifacts: `constitution.md`, `_templates/`, `NNN-<feature>/{spec,plan,tasks}.md`
- `.claude/skills/` Agent skills; `.claude/commands/` slash commands (`/specify`, `/plan`, `/implement`)

## Commands

- Format: `terraform -chdir=infra fmt -recursive`
- Validate: `terraform -chdir=infra init -backend=false && terraform -chdir=infra validate`
- Lint: `tflint --chdir=infra`
- Security scan: `checkov -d infra`
- Plan: `terraform -chdir=infra plan -var-file=env/dev.tfvars`
- Tests: `pytest tests`

## Workflow: Spec-Driven Development

- Every feature or infra change goes spec -> plan -> tasks -> implement, with user approval between phases. See the `spec-driven-development` skill.
- Do not write code or Terraform until `specs/NNN-<feature>/` spec, plan, and tasks exist and are approved.
- Plans must comply with `specs/constitution.md`. If code and spec diverge, update the spec first.

## Conventions

- Terraform >= 1.10, AWS provider ~> 6.0; pin versions in `versions.tf`.
- Resource names: `${var.project}-${var.environment}-<name>`; tag everything via provider `default_tags`.
- Each Lambda gets its own least-privilege IAM role; never use `*` actions/resources without justification.
- Explicit CloudWatch log groups with retention set.
- No secrets in code or tfvars; use SSM Parameter Store / Secrets Manager.
- Remote state in S3 with native locking (`use_lockfile`); never commit `*.tfstate`.
- Python 3.12 runtime, handlers named `handler.lambda_handler`.

## Safety rules for agents

- Run `fmt`, `validate`, and `plan` freely. NEVER run `terraform apply` or `destroy` without explicit user approval.
- Do not modify `backend.tf` or state resources without asking.
- Add a new endpoint using the `terraform-lambda-endpoint` skill.
