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

data "archive_file" "ingestion_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../backend/lambda"
  output_path = "${path.module}/../../backend/.terraform/ingestion_lambda.zip"

  excludes = [".gitignore", "*.pyc", "__pycache__"]
}

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
          "sns:Publish"
        ]
        Resource = aws_sns_topic.ingestion_failures.arn
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

resource "aws_lambda_function" "ingestion" {
  function_name = "chess-warehouse-ingestion"
  role          = aws_iam_role.ingestion_lambda.arn
  handler = "handler.lambda_handler"
  runtime = "python3.11"
  filename         = data.archive_file.ingestion_lambda.output_path
  source_code_hash = data.archive_file.ingestion_lambda.output_base64sha256

  environment {
    variables = {
      DYNAMODB_TABLE     = aws_dynamodb_table.chess_games.name
      S3_RAW_BUCKET      = aws_s3_bucket.raw_games.id
      LICHESS_USERNAME   = var.lichess_username
      SSM_PARAMETER_NAME = aws_ssm_parameter.lichess_token.name
    }
  }

  timeout = 300
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

resource "aws_cloudwatch_log_group" "ingestion_lambda" {
  name              = "/aws/lambda/chess-warehouse-ingestion"
  retention_in_days = 7

  tags = {
    Name = "chess-warehouse-ingestion"
  }
}

# Data source: current AWS account ID (used in ARN construction)
data "aws_caller_identity" "current" {}

# SNS topic to receive ingestion failure notifications.
resource "aws_sns_topic" "ingestion_failures" {
  name = "chess-warehouse-ingestion-failures"
}

# Optional SNS subscription for email alerts on ingestion failures.
resource "aws_sns_topic_subscription" "ingestion_failure_email" {
  count      = var.failure_alert_email != "" ? 1 : 0
  topic_arn  = aws_sns_topic.ingestion_failures.arn
  protocol   = "email"
  endpoint   = var.failure_alert_email
}

# EventBridge rule (cron) to trigger the ingestion Lambda nightly.
resource "aws_cloudwatch_event_rule" "nightly_ingestion" {
  name                = "chess-warehouse-nightly-ingestion"
  description         = "Trigger the ingestion Lambda nightly"
  schedule_expression = var.ingestion_schedule_cron
}

# Allow EventBridge to invoke the Lambda function
resource "aws_lambda_permission" "allow_eventbridge_invoke" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.nightly_ingestion.arn
}

# EventBridge target wiring the rule to the Lambda function.
resource "aws_cloudwatch_event_target" "nightly_ingestion_target" {
  rule      = aws_cloudwatch_event_rule.nightly_ingestion.name
  target_id = "ingestion-lambda"
  arn       = aws_lambda_function.ingestion.arn
}

# Configure asynchronous invocation failure destination: SNS topic.
# This ensures failed async invocations are sent to the topic for alerting
# and manual inspection. The ingestion Lambda is invoked by EventBridge
# and configured here to send failures to SNS.
resource "aws_lambda_function_event_invoke_config" "ingestion_async_config" {
  function_name = aws_lambda_function.ingestion.function_name
  maximum_retry_attempts = 0

  destination_config {
    on_failure {
      destination = aws_sns_topic.ingestion_failures.arn
    }
  }
}

# CloudWatch alarm on Lambda errors routed to SNS
resource "aws_cloudwatch_metric_alarm" "ingestion_errors_alarm" {
  alarm_name          = "chess-warehouse-ingestion-errors"
  alarm_description   = "Alarm when the ingestion Lambda reports errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  alarm_actions       = [aws_sns_topic.ingestion_failures.arn]

  dimensions = {
    FunctionName = aws_lambda_function.ingestion.function_name
  }
}


