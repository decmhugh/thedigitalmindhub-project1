resource "aws_dynamodb_table" "agent_jobs" {
  name         = "${local.prefix}-agent-jobs"
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
    enabled = false
  }

  server_side_encryption {
    enabled = true
  }
}

locals {
  agent_worker_name = "${local.prefix}-agent-invoke-worker"
}

data "aws_iam_policy_document" "agent_invoke_worker" {
  statement {
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [local.agent_runtime_arn, "${local.agent_runtime_arn}/runtime-endpoint/*"]
  }

  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:${data.aws_partition.current.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.agent_ssm_name}"]
  }

  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.agent_uploads.arn}/u/*"]
  }

  statement {
    actions   = ["dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.agent_jobs.arn]
  }
}

module "agent_invoke_worker" {
  source      = "./modules/lambda_endpoint"
  name        = local.agent_worker_name
  source_dir  = "${path.root}/../src/agent_invoke_worker"
  timeout     = 300
  memory_size = 256
  environment = {
    AGENT_RUNTIME_ARN_PARAM = local.agent_ssm_name
    JOBS_TABLE              = aws_dynamodb_table.agent_jobs.name
    UPLOAD_BUCKET           = aws_s3_bucket.agent_uploads.bucket
    LOG_LEVEL               = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.agent_invoke_worker.json
}

# A failed worker run must not repeat the agent call.
resource "aws_lambda_function_event_invoke_config" "agent_invoke_worker" {
  function_name          = module.agent_invoke_worker.function_name
  maximum_retry_attempts = 0
}

data "aws_iam_policy_document" "agent_job_status" {
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.auth.arn, aws_dynamodb_table.agent_jobs.arn]
  }
}

module "agent_job_status" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-agent-job-status"
  source_dir   = "${path.root}/../src/agent_job_status"
  route_key    = "GET /agent/jobs/{job_id}"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  timeout      = 10
  memory_size  = 128
  environment = {
    AUTH_TABLE = aws_dynamodb_table.auth.name
    JOBS_TABLE = aws_dynamodb_table.agent_jobs.name
    LOG_LEVEL  = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.agent_job_status.json
}
