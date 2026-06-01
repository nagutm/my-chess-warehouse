terraform {
  backend "s3" {
    bucket         = "nagutm-resume-tfstate"
    key            = "chess-warehouse/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "resume-tf-lock"
  }

  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

provider "aws" {
  region = var.aws_region
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}