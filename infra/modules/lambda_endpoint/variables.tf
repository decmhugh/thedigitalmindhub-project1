variable "name" {
  type = string
}

variable "source_dir" {
  type = string
}

variable "route_key" {
  type = string
}

variable "api_id" {
  type = string
}

variable "api_exec_arn" {
  type = string
}

variable "environment" {
  type    = map(string)
  default = {}
}

variable "policy_json" {
  type        = string
  default     = null
  description = "Optional least-privilege IAM policy JSON for the function."
}

variable "enable_custom_policy" {
  type        = bool
  default     = false
  description = "Whether to create the custom inline policy resource."
}

variable "runtime" {
  type    = string
  default = "python3.12"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "timeout" {
  type    = number
  default = 3
}

variable "memory_size" {
  type    = number
  default = 128
}
