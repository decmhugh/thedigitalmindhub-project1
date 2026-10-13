locals {
  prefix = "${var.project}-${var.environment}"
}

resource "aws_apigatewayv2_api" "this" {
  name          = local.prefix
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  route_settings {
    route_key              = "POST /agent/invoke"
    throttling_rate_limit  = 2
    throttling_burst_limit = 5
  }

  depends_on = [module.agent_invoke]
}

module "hello" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-hello"
  source_dir   = "${path.root}/../src/hello"
  route_key    = "GET /hello"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  environment  = { LOG_LEVEL = "INFO" }
}

data "aws_partition" "current" {}

data "aws_iam_policy_document" "agent_frontend_s3" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.agent_html_bucket}/${var.agent_html_key}"]
  }
}

module "agent_frontend" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-agent-frontend"
  source_dir   = "${path.root}/../src/agent_frontend"
  route_key    = "GET /app"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  environment = {
    AGENT_HTML_BUCKET = var.agent_html_bucket
    AGENT_HTML_KEY    = var.agent_html_key
    LOG_LEVEL         = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.agent_frontend_s3.json
}
