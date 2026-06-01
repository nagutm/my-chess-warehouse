resource "aws_s3_bucket" "raw_games" {
  bucket = var.bucket_name
}

# PK: USER#<username> (all games for one user per partition)
# SK: GAME#<lastMoveAt_ms>#<gameId> (games) or META#SYNC (cursor)
resource "aws_dynamodb_table" "chess_games" {
  name         = "chess-games"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}

resource "aws_ssm_parameter" "lichess_token" {
  name  = "/chess-warehouse/lichess-token"
  type  = "SecureString"
  value = var.lichess_token

  tags = {
    Name = "lichess-token"
  }

  # Prevent accidental overwrites in CI
  lifecycle {
    ignore_changes = [value]
  }
}

# ============================================================================
# INGESTION LAMBDA (Story 2.3)
# ============================================================================
# Fetches games from Lichess, stores raw NDJSON to S3, and normalizes to DynamoDB.
# Runs nightly on schedule, incremental (cursor-based), idempotent.

# Archive the Lambda code (handler.py + dependencies from requirements.txt)
# This is computed on every plan so source code changes trigger automatic redeployment.
data "archive_file" "ingestion_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../backend/lambda"
  output_path = "${path.module}/../../backend/.terraform/ingestion_lambda.zip"

  # Include only the handler and requirements; exclude any other files
  excludes = [".gitignore", "*.pyc", "__pycache__"]
}

# IAM role for the ingestion Lambda function.
# Assumes this role when invoked; the role grants the minimum permissions needed
# to read the Lichess token, fetch games, and update the data warehouse.
resource "aws_iam_role" "ingestion_lambda" {
  name = "chess-warehouse-ingestion-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# IAM policy: least-privilege permissions for the ingestion Lambda.
# Grants:
#   - ssm:GetParameter for the Lichess token (SecureString)
#   - s3:PutObject to the raw games bucket (scoped to raw/ prefix)
#   - dynamodb:GetItem, PutItem, BatchWriteItem, Query on the chess-games table
#     (for cursor reads, game upserts, and cursor advancement)
#   - logs:CreateLogGroup, CreateLogStream, PutLogEvents for CloudWatch Logs
resource "aws_iam_role_policy" "ingestion_lambda" {
  name = "chess-warehouse-ingestion-policy"
  role = aws_iam_role.ingestion_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/chess-warehouse/lichess-token"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = "${aws_s3_bucket.raw_games.arn}/raw/lichess/*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:BatchWriteItem",
          "dynamodb:Query"
        ]
        Resource = aws_dynamodb_table.chess_games.arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/chess-warehouse-ingestion*"
      }
    ]
  })
}

# The ingestion Lambda function.
# Triggered nightly by EventBridge Scheduler (S2.6).
# Uses the archive hash to detect code changes and auto-redeploy.
resource "aws_lambda_function" "ingestion" {
  function_name = "chess-warehouse-ingestion"
  role          = aws_iam_role.ingestion_lambda.arn

  # Handler location: handler.py, function lambda_handler
  handler = "handler.lambda_handler"

  # Runtime: Python 3.11+ is recommended for cold start and feature support
  runtime = "python3.11"

  # Source code: zip file from the archive_file data source above
  filename         = data.archive_file.ingestion_lambda.output_path
  source_code_hash = data.archive_file.ingestion_lambda.output_base64sha256

  # Environment variables passed to the Lambda function
  environment {
    variables = {
      DYNAMODB_TABLE     = aws_dynamodb_table.chess_games.name
      S3_RAW_BUCKET      = aws_s3_bucket.raw_games.id
      LICHESS_USERNAME   = var.lichess_username
      SSM_PARAMETER_NAME = aws_ssm_parameter.lichess_token.name
    }
  }

  # Timeout: 15 minutes is Lambda's max, but v1 runs are small.
  # Backfill (2026-05-01 to now) is ~one request; nightly increments are tiny.
  timeout = 300

  # Memory: 256 MB is sufficient for JSON parsing and network I/O.
  memory_size = 256

  # Enable CloudWatch Logs
  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.ingestion_lambda.name
  }

  # Ensure the function is redeployed when the zip file changes
  depends_on = [
    data.archive_file.ingestion_lambda,
    aws_iam_role_policy.ingestion_lambda
  ]
}

# CloudWatch Log Group for ingestion Lambda
resource "aws_cloudwatch_log_group" "ingestion_lambda" {
  name              = "/aws/lambda/chess-warehouse-ingestion"
  retention_in_days = 7

  tags = {
    Name = "chess-warehouse-ingestion"
  }
}

# Data source: current AWS account ID (used in ARN construction)
data "aws_caller_identity" "current" {}

