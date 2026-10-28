# Tasks: Agent image build with CodeBuild

- [x] T1 Write `tests/test_build_script.py` (static checks): the script zips `agent/`, passes `IMAGE_TAG`, polls the build and exits non-zero on failure (AC5).
- [x] T2 Write `infra/agent_image_build.tf`: bucket (private, SSE, TLS-only, 7-day expiry), log group with retention, IAM role with the scoped policy, CodeBuild project on ARM_CONTAINER (AC1-AC4).
- [x] T3 Write `scripts/build-agent-image.ps1` (AC5).
- [x] T4 Run pytest, `terraform fmt`, `terraform validate`.
- [x] T5 Generate the plan with `AWS_PROFILE=project1-dev`; show it; do not apply (AC6).
- [x] T6 After explicit apply approval, run the script, confirm the tag in ECR.
- [ ] T7 Set `agent_container_image_uri` to the new tag, plan, and get approval before applying the runtime update (AC6).
