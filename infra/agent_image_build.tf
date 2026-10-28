locals {
  agent_build_name = "${local.prefix}-agent-image"
}

resource "aws_s3_bucket" "agent_build_source" {
  bucket = "${local.prefix}-agent-build-source-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "agent_build_source" {
  bucket                  = aws_s3_bucket.agent_build_source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "agent_build_source" {
  bucket = aws_s3_bucket.agent_build_source.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "agent_build_source_tls" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.agent_build_source.arn, "${aws_s3_bucket.agent_build_source.arn}/*"]

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

resource "aws_s3_bucket_policy" "agent_build_source" {
  bucket     = aws_s3_bucket.agent_build_source.id
  policy     = data.aws_iam_policy_document.agent_build_source_tls.json
  depends_on = [aws_s3_bucket_public_access_block.agent_build_source]
}

resource "aws_s3_bucket_lifecycle_configuration" "agent_build_source" {
  bucket = aws_s3_bucket.agent_build_source.id

  rule {
    id     = "expire-sources"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }
  }
}

resource "aws_cloudwatch_log_group" "agent_image_build" {
  name              = "/aws/codebuild/${local.agent_build_name}"
  retention_in_days = 14
}

data "aws_iam_policy_document" "agent_image_build_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "agent_image_build" {
  # GetAuthorizationToken does not support resource-level permissions.
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.agent.arn]
  }

  statement {
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.agent_build_source.arn}/*"]
  }

  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [aws_cloudwatch_log_group.agent_image_build.arn, "${aws_cloudwatch_log_group.agent_image_build.arn}:*"]
  }
}

resource "aws_iam_role" "agent_image_build" {
  name               = "${local.agent_build_name}-codebuild"
  assume_role_policy = data.aws_iam_policy_document.agent_image_build_assume.json
}

resource "aws_iam_role_policy" "agent_image_build" {
  name   = "build-and-push"
  role   = aws_iam_role.agent_image_build.id
  policy = data.aws_iam_policy_document.agent_image_build.json
}

resource "aws_codebuild_project" "agent_image" {
  name          = local.agent_build_name
  service_role  = aws_iam_role.agent_image_build.arn
  build_timeout = 20

  source {
    type     = "S3"
    location = "${aws_s3_bucket.agent_build_source.bucket}/agent.zip"
    buildspec = yamlencode({
      version = 0.2
      phases = {
        pre_build = {
          commands = [
            "aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $${ECR_REPO_URL%%/*}",
          ]
        }
        build = {
          commands = ["docker build -t $ECR_REPO_URL:$IMAGE_TAG ."]
        }
        post_build = {
          commands = ["docker push $ECR_REPO_URL:$IMAGE_TAG"]
        }
      }
    })
  }

  environment {
    type            = "ARM_CONTAINER"
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/amazonlinux-aarch64-standard:3.0"
    privileged_mode = true

    environment_variable {
      name  = "ECR_REPO_URL"
      value = aws_ecr_repository.agent.repository_url
    }

    environment_variable {
      name  = "AWS_REGION"
      value = var.region
    }

    environment_variable {
      name  = "IMAGE_TAG"
      value = "latest"
    }
  }

  artifacts {
    type = "NO_ARTIFACTS"
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.agent_image_build.name
    }
  }
}
