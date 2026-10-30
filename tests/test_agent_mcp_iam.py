from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agent_mcp_policy_can_read_runtime_parameter_and_invoke_scoped_runtime():
    policy = (ROOT / "infra" / "agent_mcp.tf").read_text(encoding="utf-8")

    assert 'actions   = ["ssm:GetParameter"]' in policy
    assert (
        'resources = ["arn:${data.aws_partition.current.partition}:ssm:${var.region}:'
        '${data.aws_caller_identity.current.account_id}:parameter${local.agent_ssm_name}"]'
    ) in policy
    assert 'actions   = ["bedrock-agentcore:InvokeAgentRuntime"]' in policy
    assert (
        'resources = [local.agent_runtime_arn, "${local.agent_runtime_arn}/runtime-endpoint/*"]'
    ) in policy
