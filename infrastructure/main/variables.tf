variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  type        = string
  description = "GitHub repo in owner/name format"
}