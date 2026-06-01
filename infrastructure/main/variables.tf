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

variable "bucket_name" {
  type        = string
  description = "Name of the S3 bucket used for raw chess data storage"
}