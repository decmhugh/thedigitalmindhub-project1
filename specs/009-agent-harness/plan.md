# Plan: Agent harness

Spec: ./spec.md

## Design

Finding: the runtime is not OTEL-instrumented. `agent/requirements.txt` has no OpenTelemetry package and the Dockerfile starts plain `python -m agent.app`. The runtime role already has X-Ray and `bedrock-agentcore` metric permissions.

1. Observability
   - Add `aws-opentelemetry-distro`; Dockerfile CMD becomes `opentelemetry-instrument python -m agent.app`.
   - Runtime env vars: `OTEL_PYTHON_DISTRO=aws_distro`, `OTEL_PYTHON_CONFIGURATOR=aws_configurator`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_TRACES_EXPORTER=otlp`, `AGENT_OBSERVABILITY_ENABLED=true`.
   - `infra/agent_harness.tf`: logs resource policy allowing X-Ray to write spans, plus the eval invoke policy document.
   - Transaction Search has no Terraform resource, so it is enabled by a documented CLI step.
   - No prompt/response content capture.
2. Evaluation harness
   - `evals/cases.json` (5+ cases: `prompt`, `expect_contains`, optional `expect_tool`).
   - `scripts/run_evals.py`: invokes the runtime, checks cases, publishes `Passed`/`Failed`/`PassRate` to namespace `<prefix>/agent-evals`, exits 1 on failure.
   - Output `agent_runtime_arn`; dashboard widget in `observability.tf`.
3. Harness.io CI/CD
   - `.harness/pipeline.yaml`: pytest, fmt -check, validate, tflint, checkov, plan. No apply.
   - `docs/harness-delegate-policy.md`: minimum plan permissions for the delegate's AWS role.

## IAM (least privilege)

| Action | Resource | Justification |
| ------ | -------- | ------------- |
| `logs:PutLogEvents`, `logs:CreateLogStream` | Resource policy for `xray.amazonaws.com` on `aws/spans` and `/aws/application-signals/data` in this account | Transaction Search span ingestion |
| `bedrock-agentcore:InvokeAgentRuntime` | Runtime ARN and `/runtime-endpoint/*` | Eval runner, policy document output for the developer |
| `cloudwatch:PutMetricData` | `*` with namespace condition `<prefix>/agent-evals` | Eval metrics |

## Terraform changes

New `infra/agent_harness.tf`; edit `agent_runtime.tf`, `outputs.tf`, `observability.tf`. No `backend.tf` change, no apply.

## Constitution check

- [x] One Lambda per endpoint (none added)
- [x] Least-privilege IAM
- [x] No secrets in repo
- [x] Log group with retention

## Risks / alternatives

- Image must be rebuilt and redeployed before traces appear.
- Transaction Search is a manual CLI step.
- Deterministic evals can be flaky on wording; keep `expect_contains` loose.
- Harness YAML may need tweaks for your setup.
