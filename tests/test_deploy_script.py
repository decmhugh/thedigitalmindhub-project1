from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "deploy.ps1").read_text(encoding="utf-8")


def test_script_is_utf8_text():
    assert "\x00" not in SCRIPT


def test_apply_requires_typed_confirmation_and_is_skippable():
    assert "-cne \"APPLY\"" in SCRIPT
    assert "SkipApply" in SCRIPT


def test_steps_are_present():
    for text in ("pytest", "fmt -check", "validate", "build-agent-image.ps1", "plan", "apply",
                 "s3api head-object", "update-trace-segment-destination", "run_evals.py"):
        assert text in SCRIPT


def test_harness_uses_environment_not_embedded_secrets():
    assert "HARNESS_API_KEY" in SCRIPT
    assert "x-api-key" in SCRIPT
    assert "pipeline/execute" in SCRIPT


def test_no_destroy():
    assert "destroy" not in SCRIPT.lower()


def test_build_script_emits_image_uri():
    build = (ROOT / "scripts" / "build-agent-image.ps1").read_text(encoding="utf-8")
    assert "Write-Output" in build
