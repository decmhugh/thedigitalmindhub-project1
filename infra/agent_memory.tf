resource "aws_bedrockagentcore_memory" "agent" {
  name                  = "${replace(local.prefix, "-", "_")}_agent_memory"
  description           = "Short-term session memory for the ${local.prefix} agent"
  event_expiry_duration = 7
}
