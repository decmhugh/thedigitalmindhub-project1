resource "aws_bedrock_guardrail" "agent" {
  name                      = "${local.prefix}-agent"
  blocked_input_messaging   = "This request was blocked by the safety policy."
  blocked_outputs_messaging = "This response was blocked by the safety policy."

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]

      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }

    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = ["EMAIL", "PHONE", "NAME", "CREDIT_DEBIT_CARD_NUMBER", "AWS_ACCESS_KEY", "AWS_SECRET_KEY", "PASSWORD"]

      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }
}

resource "aws_bedrock_guardrail_version" "agent" {
  guardrail_arn = aws_bedrock_guardrail.agent.guardrail_arn
  description   = "Managed by Terraform"
}
