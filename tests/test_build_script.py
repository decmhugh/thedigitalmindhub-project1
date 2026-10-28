from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "build-agent-image.ps1").read_text(encoding="utf-8")
TF = (ROOT / "infra" / "agent_image_build.tf").read_text(encoding="utf-8")


def test_script_zips_agent_directory_and_uploads_to_s3():
    assert "Compress-Archive" in SCRIPT
    assert "agent" in SCRIPT
    assert "aws s3 cp" in SCRIPT or "s3 cp" in SCRIPT
    assert "__pycache__" in SCRIPT


def test_script_passes_image_tag_and_starts_build():
    assert "IMAGE_TAG" in SCRIPT
    assert "start-build" in SCRIPT
    assert "rev-parse --short HEAD" in SCRIPT


def test_script_polls_build_and_fails_on_unsuccessful_build():
    assert "batch-get-builds" in SCRIPT
    assert "SUCCEEDED" in SCRIPT
    assert "exit 1" in SCRIPT
    assert "TAG:" in SCRIPT


def test_codebuild_project_is_native_arm64_with_log_group():
    assert "ARM_CONTAINER" in TF
    assert "aarch64" in TF
    assert "aws_cloudwatch_log_group" in TF
    assert "retention_in_days" in TF


def test_build_source_bucket_is_private_encrypted_and_expiring():
    assert "aws_s3_bucket_public_access_block" in TF
    assert "AES256" in TF
    assert "aws:SecureTransport" in TF
    assert "expiration" in TF


def test_role_is_scoped_to_agent_repository():
    assert "aws_ecr_repository.agent.arn" in TF
    assert "ecr:GetAuthorizationToken" in TF
    assert "ecr:PutImage" in TF
