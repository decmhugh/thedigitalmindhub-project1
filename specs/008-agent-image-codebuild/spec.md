# Spec: Agent image build with CodeBuild

Status: Approved

## Problem / Why

The agent runtime image is built and pushed from a developer machine with Docker Desktop. That is unreliable (engine fails to start) and not repeatable. Builds should run in AWS so any authorised developer can redeploy the agent without a local container engine.

## User stories

- As a developer, I want to start an AWS build of the `agent/` directory so that I get a new arm64 image in ECR without local Docker.
- As a developer, I want the build to print the resulting image tag so that I can set `agent_container_image_uri` and plan.

## API contract

No HTTP endpoint. Interface is a CodeBuild project `${prefix}-agent-image`, started with `aws codebuild start-build`.

| Input | Description |
| ----- | ----------- |
| Source | `agent/` packaged as a zip and uploaded to S3 by the developer (`aws s3 cp`), under a build-source bucket prefix |
| Env override `IMAGE_TAG` | Tag to push, e.g. `<git-sha>-<yyyymmddHHMMSS>` |

| Output | Description |
| ------ | ----------- |
| ECR image | `${ecr_repository_url}:${IMAGE_TAG}`, `linux/arm64`, single manifest |
| Build log | CloudWatch log group with retention set |

## Acceptance criteria

- AC1: Starting the build with a source zip and `IMAGE_TAG` pushes an arm64 image tagged `IMAGE_TAG` to the existing `project1-dev-agent` ECR repository.
- AC2: The build runs on an arm64 CodeBuild environment (no emulation) using CodeBuild's standard image Docker support.
- AC3: The CodeBuild role can push only to the agent ECR repository, read only the build-source prefix, and write only its own log group.
- AC4: Build logs go to an explicit log group with retention set.
- AC5: A script in the repo starts a build (zip, upload, start, wait) and prints the final tag.
- AC6: The build does not change the runtime; deploying the new tag stays a separate reviewed `plan` and approved `apply`.

## Non-functional

- No secrets; ECR auth uses the CodeBuild role.
- Source bucket private, encrypted, public access blocked, TLS-only, objects expire after 7 days.
- Build timeout 20 minutes; small arm64 compute type.
- Cost: pay per build minute only; no standing resources beyond the project, bucket and log group.

## Out of scope

- Automatic builds on git push (CI/CD).
- Image scanning policy changes.
- Changing the runtime or its image URI in Terraform (done after the build, via `dev.tfvars`).

## Decisions

- Source is an S3 zip upload (no GitHub connection).
- A separate small build-source bucket is used, for least privilege.
