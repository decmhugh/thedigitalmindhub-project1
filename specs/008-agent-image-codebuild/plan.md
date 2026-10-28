# Plan: Agent image build with CodeBuild

Spec: [spec.md](./spec.md)

## Approach

No Lambda or API route (constitution #2 not affected). Terraform adds a CodeBuild project, a private source bucket, a log group and an IAM role in `infra/agent_image_build.tf`. A PowerShell script `scripts/build-agent-image.ps1` zips `agent/`, uploads it, starts the build, waits and prints the tag.

## Resources (`infra/agent_image_build.tf`)

| Resource | Notes |
| -------- | ----- |
| `aws_s3_bucket.agent_build_source` | `${prefix}-agent-build-source-<account>`; public access block, SSE-S3, TLS-only policy, 7-day expiry |
| `aws_cloudwatch_log_group.agent_image_build` | `/aws/codebuild/${prefix}-agent-image`, 14-day retention |
| `aws_iam_role.agent_image_build` | Trust `codebuild.amazonaws.com` |
| `aws_codebuild_project.agent_image` | `ARM_CONTAINER`, `BUILD_GENERAL1_SMALL`, image `aws/codebuild/amazonlinux-aarch64-standard:3.0`, `privileged_mode = true` (required for Docker), source `S3` `bucket/agent.zip`, 20 min timeout, env vars `ECR_REPO_URL`, `AWS_REGION` |

Buildspec (inline): login to ECR with `aws ecr get-login-password`, `docker build -t $ECR_REPO_URL:$IMAGE_TAG .`, `docker push`. Native arm64, no emulation, single manifest.

## IAM (least privilege)

| Statement | Actions | Resources |
| --------- | ------- | --------- |
| ECR auth | `ecr:GetAuthorizationToken` | `*` (API does not support resource scoping) |
| ECR push | `ecr:BatchCheckLayerAvailability`, `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage`, `BatchGetImage`, `GetDownloadUrlForLayer` | `aws_ecr_repository.agent.arn` |
| Source | `s3:GetObject`, `s3:GetObjectVersion` | `bucket/*` |
| Logs | `logs:CreateLogStream`, `logs:PutLogEvents` | the log group ARN and `:*` |

The developer running the script uses their own SSO credentials (already admin); no new human permissions.

## Script

`scripts/build-agent-image.ps1 -Profile project1-dev`: tag = `<git short sha>-<yyyyMMddHHmmss>`, zip `agent/` (excluding `__pycache__`), `aws s3 cp`, `aws codebuild start-build --environment-variables-override`, poll `batch-get-builds` until done, print `TAG:` and the full image URI, exit non-zero on failure.

## Observability and cost

Explicit log group with retention. Pay per build minute (small arm64). Bucket objects expire in 7 days.

## Risks

- `privileged_mode` is needed for Docker builds; the role is scoped to one ECR repo, bucket and log group.
- CodeBuild arm64 availability in `eu-west-1` (supported).
- Zip must contain `Dockerfile` at its root (zip the contents of `agent/`).

## Constitution check

Spec first (yes), IaC only (yes), least privilege (yes), no secrets (yes), observable (yes), safe changes (plan reviewed, apply approved).
