from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "agent" / "Dockerfile").read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "agent" / "requirements.txt").read_text(encoding="utf-8")
RUNTIME_TF = (ROOT / "infra" / "agent_runtime.tf").read_text(encoding="utf-8")
HARNESS_TF = (ROOT / "infra" / "agent_harness.tf").read_text(encoding="utf-8")
PIPELINE = (ROOT / ".harness" / "pipeline.yaml").read_text(encoding="utf-8")


def test_agent_is_otel_instrumented():
    assert "aws-opentelemetry-distro" in REQUIREMENTS
    assert "opentelemetry-instrument" in DOCKERFILE
    assert "AGENT_OBSERVABILITY_ENABLED" in RUNTIME_TF
    assert "OTEL_PYTHON_DISTRO" in RUNTIME_TF


def test_content_capture_is_not_enabled():
    for text in (DOCKERFILE, RUNTIME_TF):
        assert "CAPTURE_MESSAGE_CONTENT" not in text


def test_spans_policy_is_scoped_to_account_and_xray():
    assert "xray.amazonaws.com" in HARNESS_TF
    assert "aws:SourceAccount" in HARNESS_TF
    assert "aws_cloudwatch_log_resource_policy" in HARNESS_TF


def test_pipeline_runs_checks_and_never_applies():
    for step in ("pytest tests", "fmt", "validate", "tflint", "checkov", "plan"):
        assert step in PIPELINE
    assert "terraform apply" not in PIPELINE
    assert "terraform destroy" not in PIPELINE
