variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  type        = string
  description = "GitHub repo in owner/name format"
}

variable "lichess_token" {
  type        = string
  sensitive   = true
  description = "Lichess personal access token (never log or commit this)"
}

variable "lichess_username" {
  type        = string
  description = "Lichess username to fetch games for"
}

variable "bucket_name" {
  type        = string
  description = "Name of the S3 bucket used for raw chess data storage"
}

variable "ingestion_schedule_cron" {
  type        = string
  description = "Cron expression for the nightly ingestion schedule"
  default     = "cron(0 3 * * ? *)"
}

variable "failure_alert_email" {
  type        = string
  description = "Optional email address to subscribe to ingestion failure alerts"
  default     = ""
}