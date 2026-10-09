---
name: spec-driven-development
description: Spec-driven workflow for this project. Use for any new feature, endpoint, or infrastructure change; write and approve spec, plan, and tasks under specs/ before writing code or Terraform.
---

# Spec-driven development

Specs in `specs/NNN-<feature>/` are the source of truth. Code and Terraform follow the spec, never the reverse.

## Phases (stop for user approval after each)

1. **Specify** - copy `specs/_templates/spec.md` to `specs/NNN-<feature>/spec.md`. Describe WHAT and WHY: user stories, API contract (method, path, request/response, errors), acceptance criteria. No implementation details. Mark unknowns `[NEEDS CLARIFICATION]` and ask the user.
2. **Plan** - `plan.md` from template. HOW: Lambda per endpoint, IAM least-privilege permissions, env vars, SSM/Secrets usage, observability, cost/security notes. Check against `specs/constitution.md`.
3. **Tasks** - `tasks.md` from template. Small ordered checklist; each task references a spec acceptance criterion. Mark `[P]` if parallelizable.
4. **Implement** - work tasks in order, tests first (pytest per acceptance criterion), then Terraform via the `terraform-lambda-endpoint` skill. Tick tasks as done. Run fmt/validate/tflint/checkov/pytest and show `plan`; never apply.

## Rules

- If implementation reveals a spec gap, update the spec first, then continue.
- Every acceptance criterion maps to at least one test.
- Use next free `NNN` number; keep specs short.
