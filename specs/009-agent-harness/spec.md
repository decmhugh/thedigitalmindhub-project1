# Spec: Agent harness (observability, evaluation, Harness.io CI/CD)

Status: Approved

## Problem / Why

The agent runs on AgentCore but there is no way to see its traces in AWS, no repeatable way to check its quality, and no pipeline that validates infra changes. Existing monitoring ([observability.tf](../../infra/observability.tf)) covers only API Gateway and the invoke Lambda.

## User stories

- As an operator, I want agent traces, tool calls, and runtime metrics in CloudWatch GenAI Observability so that I can see what the agent does in AWS.
- As a developer, I want a repeatable evaluation harness that runs fixed test cases against the agent so that regressions in answers or tool use are caught and scores are visible in AWS.
- As a developer, I want a Harness.io pipeline that runs fmt, validate, tflint, checkov, pytest, and plan so that changes are verified before anyone applies them.

## API contract

No new public API endpoints. New infrastructure and tooling only.

| Component | Interface | Result |
| --------- | --------- | ------ |
| Observability | AgentCore runtime emits OTEL traces/metrics to CloudWatch (Transaction Search enabled) | Traces and agent metrics visible in CloudWatch GenAI Observability |
| Evaluation | `scripts/run-evals.py` invokes the runtime with cases from `evals/cases.json` | Pass/fail per case, scores published as CloudWatch custom metrics, dashboard widget |
| CI/CD | `.harness/pipeline.yaml` | Stages: lint/test, scan, plan. No apply stage |

## Acceptance criteria

- AC1: After an agent invocation, a trace with model and tool spans appears in CloudWatch GenAI Observability within minutes.
- AC2: Prompts, session IDs, and phone numbers are not recorded in traces or logs.
- AC3: `evals/cases.json` holds at least 5 cases (expected substring or expected guardrail block; tool use isn't exposed in the runtime response); the runner exits non-zero if any case fails.
- AC4: Eval results appear as CloudWatch metrics in namespace `<prefix>/agent-evals` and on the agent dashboard.
- AC5: The Harness pipeline runs `terraform fmt -check`, `validate`, `tflint`, `checkov`, `pytest tests`, and `plan`; it never runs `apply` or `destroy`.
- AC6: No static secrets in the repo; the pipeline uses an existing Harness delegate with its own AWS role. Terraform documents the minimum permissions that role needs for plan.
- AC7: All new IAM is least-privilege, names follow `${var.project}-${var.environment}-<name>`, log groups have retention.
- AC8: `terraform fmt`, `validate`, and `checkov` pass; unit tests added for the eval runner.

## Non-functional

- Trace data is sensitive: redact prompt and response content; retention set explicitly.
- Evals run on demand, so cost is bounded by the number of cases.

## Out of scope

- Terraform `apply` from the pipeline.
- Creating the Harness.io account, project, or delegate (already exist).
- LLM-as-judge or AgentCore built-in evaluators.

## Open questions

- Resolved: deterministic eval checks; existing delegate with own AWS role.
- To verify in plan phase: whether the runtime is already OTEL-instrumented ([agent/requirements.txt](../../agent/requirements.txt)).
