resource "aws_ecr_repository" "agent" {
  name                 = "${local.prefix}-agent"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

data "aws_iam_policy_document" "agent_runtime_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent_runtime" {
  name               = "${local.prefix}-agentcore-runtime"
  assume_role_policy = data.aws_iam_policy_document.agent_runtime_assume_role.json
}

data "aws_iam_policy_document" "agent_runtime_ecr" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.agent.arn]
  }
}

resource "aws_iam_role_policy" "agent_runtime_ecr" {
  name   = "ecr-image-pull"
  role   = aws_iam_role.agent_runtime.id
  policy = data.aws_iam_policy_document.agent_runtime_ecr.json
}

locals {
  agent_model_id = "amazon.nova-pro-v1:0"
}

data "aws_iam_policy_document" "agent_runtime_tools" {
  # The EU inference profile routes across EU regions, so the foundation-model ARN needs a region wildcard.
  statement {
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:${data.aws_partition.current.partition}:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/eu.${local.agent_model_id}",
      "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/${local.agent_model_id}",
    ]
  }

  statement {
    actions = [
      "bedrock-agentcore:StartCodeInterpreterSession",
      "bedrock-agentcore:InvokeCodeInterpreter",
      "bedrock-agentcore:StopCodeInterpreterSession",
      "bedrock-agentcore:GetCodeInterpreterSession",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.region}:aws:code-interpreter/aws.codeinterpreter.v1"]
  }

  statement {
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.agent.guardrail_arn]
  }

  statement {
    actions = [
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:GetEvent",
      "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:DeleteEvent",
      "bedrock-agentcore:ListSessions",
      "bedrock-agentcore:ListActors",
    ]
    resources = [aws_bedrockagentcore_memory.agent.arn]
  }

  statement {
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.agent.gateway_arn]
  }

  statement {
    actions = [
      "bedrock-agentcore:StartBrowserSession",
      "bedrock-agentcore:GetBrowserSession",
      "bedrock-agentcore:StopBrowserSession",
      "bedrock-agentcore:ConnectBrowserAutomationStream",
      "bedrock-agentcore:UpdateBrowserStream",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.region}:aws:browser/aws.browser.v1"]
  }

  statement {
    actions   = ["logs:CreateLogGroup", "logs:DescribeLogStreams"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }

  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]
  }

  statement {
    actions   = ["logs:PutResourcePolicy"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/runtimes/${local.agent_runtime_name}-*"]
  }

  statement {
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }

  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }
}

resource "aws_iam_role_policy" "agent_runtime_tools" {
  name   = "model-and-tools"
  role   = aws_iam_role.agent_runtime.id
  policy = data.aws_iam_policy_document.agent_runtime_tools.json
}

resource "aws_bedrockagentcore_agent_runtime" "agent" {
  count = var.agent_container_image_uri == "" ? 0 : 1

  agent_runtime_name = "${replace(local.prefix, "-", "_")}_agent"
  description        = "${local.prefix} LangGraph agent"
  role_arn           = aws_iam_role.agent_runtime.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = var.agent_container_image_uri
    }
  }

  environment_variables = merge(var.agent_runtime_environment, {
    MODEL_ID          = "eu.${local.agent_model_id}"
    LOG_LEVEL         = "INFO"
    GUARDRAIL_ID      = aws_bedrock_guardrail.agent.guardrail_id
    GUARDRAIL_VERSION = aws_bedrock_guardrail_version.agent.version
    MEMORY_ID         = aws_bedrockagentcore_memory.agent.id
    GATEWAY_URL       = aws_bedrockagentcore_gateway.agent.gateway_url
  })

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  depends_on = [aws_iam_role_policy.agent_runtime_ecr, aws_iam_role_policy.agent_runtime_tools]
}