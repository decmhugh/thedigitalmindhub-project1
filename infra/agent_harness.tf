locals {
  agent_evals_namespace = "${local.prefix}/agent-evals"
}

# Lets X-Ray deliver spans to CloudWatch Logs for Transaction Search (GenAI Observability).
data "aws_iam_policy_document" "xray_spans" {
  statement {
    sid     = "XRayWriteSpans"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:aws/spans:*",
      "arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/application-signals/data:*",
    ]

    principals {
      type        = "Service"
      identifiers = ["xray.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:xray:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "xray_spans" {
  policy_name     = "${local.prefix}-xray-spans"
  policy_document = data.aws_iam_policy_document.xray_spans.json
}

# Attach to whichever developer/CI identity runs scripts/run_evals.py.
data "aws_iam_policy_document" "agent_evals_runner" {
  statement {
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = var.agent_container_image_uri == "" ? [] : [
      aws_bedrockagentcore_agent_runtime.agent[0].agent_runtime_arn,
      "${aws_bedrockagentcore_agent_runtime.agent[0].agent_runtime_arn}/runtime-endpoint/*",
    ]
  }

  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.agent_evals_namespace]
    }
  }
}

output "agent_evals_runner_policy_json" {
  description = "IAM policy for the identity that runs scripts/run_evals.py."
  value       = data.aws_iam_policy_document.agent_evals_runner.json
}

output "agent_evals_namespace" {
  value = local.agent_evals_namespace
}
