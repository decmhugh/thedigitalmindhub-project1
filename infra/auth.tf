locals {
  auth_pepper_param = "/${var.project}/${var.environment}/auth/pepper"
  auth_table_arn    = aws_dynamodb_table.auth.arn
  auth_pepper_arn   = "arn:${data.aws_partition.current.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.auth_pepper_param}"
  sms_sender_arn    = "arn:${data.aws_partition.current.partition}:sms-voice:${var.region}:${data.aws_caller_identity.current.account_id}:sender-id/${var.sms_sender_id}/${var.sms_sender_country}"
}

# Holds OTP code hashes, user records, and sessions; items expire through the TTL attribute.
resource "aws_dynamodb_table" "auth" {
  name         = "${local.prefix}-auth"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "random_password" "auth_pepper" {
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "auth_pepper" {
  name  = local.auth_pepper_param
  type  = "SecureString"
  value = random_password.auth_pepper.result
}

data "aws_iam_policy_document" "auth_start" {
  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [local.auth_table_arn]
  }

  statement {
    actions   = ["ssm:GetParameter"]
    resources = [local.auth_pepper_arn]
  }

  statement {
    actions   = ["sms-voice:SendTextMessage"]
    resources = [local.sms_sender_arn]
  }
}

data "aws_iam_policy_document" "auth_verify" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [local.auth_table_arn]
  }

  statement {
    actions   = ["ssm:GetParameter"]
    resources = [local.auth_pepper_arn]
  }
}

data "aws_iam_policy_document" "auth_session" {
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [local.auth_table_arn]
  }
}

data "aws_iam_policy_document" "auth_logout" {
  statement {
    actions   = ["dynamodb:DeleteItem"]
    resources = [local.auth_table_arn]
  }
}

module "auth_start" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-auth-start"
  source_dir   = "${path.root}/../src/auth_start"
  route_key    = "POST /auth/start"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  timeout      = 10
  environment = {
    AUTH_TABLE        = aws_dynamodb_table.auth.name
    AUTH_PEPPER_PARAM = local.auth_pepper_param
    SMS_SENDER_ID     = var.sms_sender_id
    LOG_LEVEL         = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.auth_start.json
}

module "auth_verify" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-auth-verify"
  source_dir   = "${path.root}/../src/auth_verify"
  route_key    = "POST /auth/verify"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  timeout      = 10
  environment = {
    AUTH_TABLE        = aws_dynamodb_table.auth.name
    AUTH_PEPPER_PARAM = local.auth_pepper_param
    LOG_LEVEL         = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.auth_verify.json
}

module "auth_session" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-auth-session"
  source_dir   = "${path.root}/../src/auth_session"
  route_key    = "GET /auth/session"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  environment = {
    AUTH_TABLE = aws_dynamodb_table.auth.name
    LOG_LEVEL  = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.auth_session.json
}

module "auth_logout" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-auth-logout"
  source_dir   = "${path.root}/../src/auth_logout"
  route_key    = "POST /auth/logout"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  environment = {
    AUTH_TABLE = aws_dynamodb_table.auth.name
    LOG_LEVEL  = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.auth_logout.json
}
