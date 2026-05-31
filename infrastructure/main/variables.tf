variable "bucket_name" {
  type        = string
  description = "Globally unique name for the site bucket"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  type        = string
  description = "GitHub repo in owner/name format"
}