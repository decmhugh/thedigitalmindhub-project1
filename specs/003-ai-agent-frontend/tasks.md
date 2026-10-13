# Tasks: Anonymous agent frontend demo

- [ ] T0 Review and approve the revised 003 spec, plan, and tasks
- [x] T1 Existing S3 Lambda tests cover object retrieval, headers, and generic read failures
- [ ] T2 Remove sign-in/password UI and Cognito calls from `src/agent_frontend/agent.html`
- [ ] T3 Add/update frontend tests for anonymous requests, session reuse, and 429 display
- [x] T4 Existing `GET /app` Lambda route reads the configured S3 object with least-privilege access
- [x] T5 Existing deployment sync is scoped to `s3://dmh-app-prod1-bucket/html/` and does not use `--delete`
- [ ] T6 Remove Cognito client ID from `infra/env/dev.tfvars`, Terraform output, and `deploy.ps1` substitution
- [ ] T7 Implement anonymous invoke and API Gateway route throttling per spec 002
- [ ] T8 Run pytest, Terraform fmt/validate, tflint, and checkov
- [ ] T9 Review plan; do not apply
- [ ] T10 After approved apply, test `GET /app` and anonymous chat
