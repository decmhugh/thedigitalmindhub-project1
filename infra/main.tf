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
