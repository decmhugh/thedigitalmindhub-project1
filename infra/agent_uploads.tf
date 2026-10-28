resource "aws_s3_bucket" "agent_uploads" {
  bucket = "${local.prefix}-agent-uploads-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "agent_uploads" {
  bucket                  = aws_s3_bucket.agent_uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "agent_uploads" {
  bucket = aws_s3_bucket.agent_uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "agent_uploads_tls" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.agent_uploads.arn, "${aws_s3_bucket.agent_uploads.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "agent_uploads" {
  bucket     = aws_s3_bucket.agent_uploads.id
  policy     = data.aws_iam_policy_document.agent_uploads_tls.json
  depends_on = [aws_s3_bucket_public_access_block.agent_uploads]
}

resource "aws_s3_bucket_cors_configuration" "agent_uploads" {
  bucket = aws_s3_bucket.agent_uploads.id

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = [aws_apigatewayv2_api.this.api_endpoint]
    allowed_headers = ["*"]
    max_age_seconds = 300
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "agent_uploads" {
  bucket = aws_s3_bucket.agent_uploads.id

  rule {
    id     = "expire-uploads"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "agent_upload_url" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.agent_uploads.arn}/u/*"]
  }

  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.auth.arn]
  }
}

module "agent_upload_url" {
  source       = "./modules/lambda_endpoint"
  name         = "${local.prefix}-agent-upload-url"
  source_dir   = "${path.root}/../src/agent_upload_url"
  route_key    = "POST /agent/upload-url"
  api_id       = aws_apigatewayv2_api.this.id
  api_exec_arn = aws_apigatewayv2_api.this.execution_arn
  timeout      = 10
  memory_size  = 256
  environment = {
    UPLOAD_BUCKET = aws_s3_bucket.agent_uploads.bucket
    AUTH_TABLE    = aws_dynamodb_table.auth.name
    LOG_LEVEL     = "INFO"
  }
  enable_custom_policy = true
  policy_json          = data.aws_iam_policy_document.agent_upload_url.json
}
