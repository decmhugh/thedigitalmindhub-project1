data "aws_caller_identity" "current" {}

locals {
  agent_runtime_name = "${replace(local.prefix, "-", "_")}_agent"
  agent_runtime_arn  = "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:runtime/${local.agent_runtime_name}-*"
  agent_ssm_name     = "/${var.project}/${var.environment}/agent/runtime-arn"
}

# Holds a placeholder until the runtime exists; the invoke Lambda returns 502 meanwhile.
resource "aws_ssm_parameter" "agent_runtime_arn" {
  name  = local.agent_ssm_name
  type  = "String"
  value = try(aws_bedrockagentcore_agent_runtime.agent[0].agent_runtime_arn, "not-deployed")
}

data "aws_iam_policy_document" "agent_invoke" {
  statement {
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [local.agent_runtime_arn, "${local.agent_runtime_arn}/runtime-endpoint/*"]
  }

  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:${data.aws_partition.current.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.agent_ssm_name}"]
  }
}

module "agent_invoke" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-agent-invoke"
  source_dir   = "${path.root}/../src/agent_invoke"
  route_key    = "POST /agent/invoke"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  timeout      = 60
  memory_size  = 256
  environment = {
    AGENT_RUNTIME_ARN_PARAM = local.agent_ssm_name
    LOG_LEVEL               = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.agent_invoke.json
}
