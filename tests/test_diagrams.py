import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate(tmp_path):
    spec = importlib.util.spec_from_file_location("gen_diagrams", ROOT / "scripts" / "gen_diagrams.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--out", str(tmp_path)]) == 0
    return (tmp_path / "infrastructure.svg").read_text(encoding="utf-8"), (tmp_path / "agent.svg").read_text(encoding="utf-8")


def test_svgs_are_valid_xml_and_reflect_source(tmp_path):
    infra, agent = generate(tmp_path)
    ET.fromstring(infra)
    ET.fromstring(agent)
    assert "POST /agent/invoke" in infra
    assert "AgentCore Runtime" in infra
    assert "run_python" in agent
    assert "browse_page" in agent
    assert "say_hello" in agent
    assert "Bedrock Guardrail" in agent


def test_lambda_endpoints_come_from_terraform(tmp_path):
    infra, _ = generate(tmp_path)
    for name in ("agent-invoke", "auth-start", "hello"):
        assert name in infra


def test_sequence_diagram_covers_input_to_model(tmp_path):
    generate(tmp_path)
    sequence = (tmp_path / "sequence.svg").read_text(encoding="utf-8")
    ET.fromstring(sequence)
    for text in ("POST /agent/invoke", "validate auth_session", "InvokeAgentRuntime", "Converse", "run_python", "browse_page", "say_hello", "Guardrail"):
        assert text in sequence


def test_deploy_script_generates_diagrams():
    assert "gen_diagrams.py" in (ROOT / "deploy.ps1").read_text(encoding="utf-8")
