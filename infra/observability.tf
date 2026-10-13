locals {
  agent_invoke_function = "${local.prefix}-agent-invoke"
}

resource "aws_cloudwatch_metric_alarm" "agent_api_5xx" {
  alarm_name          = "${local.prefix}-agent-api-5xx"
  alarm_description   = "The agent API is returning server errors (502/504 from the invoke Lambda)."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiId = aws_apigatewayv2_api.this.id
    Stage = aws_apigatewayv2_stage.default.name
  }
}

resource "aws_cloudwatch_metric_alarm" "agent_invoke_errors" {
  alarm_name          = "${local.prefix}-agent-invoke-errors"
  alarm_description   = "The agent invoke Lambda is failing with unhandled errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = local.agent_invoke_function
  }
}

resource "aws_cloudwatch_dashboard" "agent" {
  dashboard_name = "${local.prefix}-agent"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Agent API requests and errors"
          region = var.region
          stat   = "Sum"
          period = 300
          view   = "timeSeries"
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.this.id, "Stage", aws_apigatewayv2_stage.default.name],
            [".", "4xx", ".", ".", ".", "."],
            [".", "5xx", ".", ".", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Invoke Lambda duration (ms)"
          region = var.region
          period = 300
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", local.agent_invoke_function, { stat = "Average" }],
            ["...", { stat = "p95" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Invoke Lambda invocations and errors"
          region = var.region
          stat   = "Sum"
          period = 300
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", local.agent_invoke_function],
            [".", "Errors", ".", "."],
          ]
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Recent invoke results (no prompts or session IDs are logged)"
          region = var.region
          view   = "table"
          query  = "SOURCE '/aws/lambda/${local.agent_invoke_function}' | fields @timestamp, status, latency_ms | filter ispresent(status) | sort @timestamp desc | limit 20"
        }
      },
    ]
  })
}
