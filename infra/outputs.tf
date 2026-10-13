output "api_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "agent_frontend_url" {
  value = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/app"
}

output "agent_ecr_repository_url" {
  value = aws_ecr_repository.agent.repository_url
}

output "agent_runtime_arn" {
  value = try(aws_bedrockagentcore_agent_runtime.agent[0].agent_runtime_arn, null)
}
