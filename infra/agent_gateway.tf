data "aws_iam_policy_document" "agent_gateway_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent_gateway" {
  name               = "${local.prefix}-agentcore-gateway"
  assume_role_policy = data.aws_iam_policy_document.agent_gateway_assume.json
}

# Add each Lambda that the gateway exposes as a tool to this list.
data "aws_iam_policy_document" "agent_gateway_tools" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [module.hello.function_arn]
  }
}

resource "aws_iam_role_policy" "agent_gateway_tools" {
  name   = "invoke-tool-lambdas"
  role   = aws_iam_role.agent_gateway.id
  policy = data.aws_iam_policy_document.agent_gateway_tools.json
}

resource "aws_bedrockagentcore_gateway" "agent" {
  name            = "${local.prefix}-agent-gateway"
  description     = "MCP tools for the ${local.prefix} agent"
  role_arn        = aws_iam_role.agent_gateway.arn
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"

  depends_on = [aws_iam_role_policy.agent_gateway_tools]
}

resource "aws_bedrockagentcore_gateway_target" "hello" {
  name               = "hello"
  description        = "Returns a greeting from the hello endpoint"
  gateway_identifier = aws_bedrockagentcore_gateway.agent.gateway_id

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = module.hello.function_arn

        tool_schema {
          inline_payload {
            name        = "say_hello"
            description = "Call the project's hello endpoint and return its greeting."

            input_schema {
              type = "object"
            }
          }
        }
      }
    }
  }
}
