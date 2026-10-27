variable "project" {
  type    = string
  default = "project1"
}

variable "environment" {
  type = string
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "agent_html_bucket" {
  type    = string
  default = "dmh-app-prod1-bucket"
}

variable "agent_html_key" {
  type    = string
  default = "html/agent.html"
}

variable "sms_sender_id" {
  type        = string
  default     = "SERV"
  description = "Registered SMS sender ID used to send sign-in codes."
}

variable "sms_sender_country" {
  type        = string
  default     = "IE"
  description = "ISO country code the sender ID is registered for."
}

variable "agent_container_image_uri" {
  type        = string
  default     = ""
  description = "Published ARM64 ECR image URI for the AgentCore runtime."
}

variable "agent_runtime_environment" {
  type        = map(string)
  default     = {}
  description = "Environment variables passed to the AgentCore runtime container."
}
