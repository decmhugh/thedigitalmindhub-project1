# Agent harness: observability, evals, Harness.io

## Observability (AgentCore traces in AWS)

1. Rebuild and deploy the agent image (`scripts/build-agent-image.ps1`, then set `agent_container_image_uri`). The image now runs under `opentelemetry-instrument`.
2. Enable Transaction Search once per account/region:
   `aws xray update-trace-segment-destination --destination CloudWatchLogs --region eu-west-1`
3. Open CloudWatch > GenAI Observability > Bedrock AgentCore to see traces. No prompt or response content is captured.

## Evals

```
python scripts/run_evals.py --runtime-arn <agent_runtime_arn> --namespace <agent_evals_namespace> --region eu-west-1
```

Both values are Terraform outputs. Attach `agent_evals_runner_policy_json` to the identity that runs it. Results appear on the `<prefix>-agent` dashboard (pass rate widget). The script exits 1 if any case fails.

## Harness.io pipeline

Import `.harness/pipeline.yaml`. It runs pytest, `fmt -check`, `validate`, `tflint`, `checkov`, and `plan` only; it never applies.

The delegate's AWS role needs, for `plan` only:
- Read-only on the resources managed here (`ReadOnlyAccess` or a scoped equivalent) plus `iam:Get*`/`iam:List*`.
- S3 on the state bucket and key: `s3:GetObject`, `s3:ListBucket`, and `s3:PutObject`/`s3:DeleteObject` on the `.tflock` lock file (native locking).
- No write access to managed resources.
