locals {
  frontend_bucket_name = "${lower(replace(var.github_repo, "/", "-"))}-frontend"
}

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
  function_name    = "chess-warehouse-ingestion"
  role             = aws_iam_role.ingestion_lambda.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.11"
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

  timeout     = 300
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

# Stats Lambda package contains the backend package so imports like backend.lambda.aggregations work.
data "archive_file" "stats_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../backend"
  output_path = "${path.module}/../../backend/.terraform/stats_lambda.zip"

  excludes = [
    ".gitignore",
    ".terraform",
    "*.pyc",
    "__pycache__",
    "tests/*",
  ]
}

resource "aws_iam_role" "stats_lambda" {
  name = "chess-warehouse-stats-lambda-role"

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

resource "aws_iam_role_policy" "stats_lambda" {
  name = "chess-warehouse-stats-policy"
  role = aws_iam_role.stats_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
        ]
        Resource = aws_dynamodb_table.chess_games.arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/chess-warehouse-stats*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "stats_lambda" {
  name              = "/aws/lambda/chess-warehouse-stats"
  retention_in_days = 7

  tags = {
    Name = "chess-warehouse-stats"
  }
}

resource "aws_lambda_function" "stats" {
  function_name    = "chess-warehouse-stats"
  role             = aws_iam_role.stats_lambda.arn
  handler          = "lambda.stats_handler.lambda_handler"
  runtime          = "python3.11"
  filename         = data.archive_file.stats_lambda.output_path
  source_code_hash = data.archive_file.stats_lambda.output_base64sha256

  environment {
    variables = {
      DYNAMODB_TABLE   = aws_dynamodb_table.chess_games.name
      LICHESS_USERNAME = var.lichess_username
    }
  }

  timeout     = 30
  memory_size = 256

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.stats_lambda.name
  }

  depends_on = [
    data.archive_file.stats_lambda,
    aws_iam_role_policy.stats_lambda,
  ]
}

resource "aws_apigatewayv2_api" "stats_api" {
  name          = "chess-warehouse-stats-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["https://${aws_cloudfront_distribution.site.domain_name}"]
    allow_methods = ["GET", "HEAD", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "stats_lambda" {
  api_id                 = aws_apigatewayv2_api.stats_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.stats.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "summary" {
  api_id    = aws_apigatewayv2_api.stats_api.id
  route_key = "GET /stats/summary"
  target    = "integrations/${aws_apigatewayv2_integration.stats_lambda.id}"
}

resource "aws_apigatewayv2_route" "openings" {
  api_id    = aws_apigatewayv2_api.stats_api.id
  route_key = "GET /stats/openings"
  target    = "integrations/${aws_apigatewayv2_integration.stats_lambda.id}"
}

resource "aws_apigatewayv2_route" "ratings" {
  api_id    = aws_apigatewayv2_api.stats_api.id
  route_key = "GET /stats/ratings"
  target    = "integrations/${aws_apigatewayv2_integration.stats_lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.stats_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "allow_api_invoke" {
  statement_id  = "AllowStatsApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stats.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.stats_api.execution_arn}/*/*"
}

resource "aws_s3_bucket" "static_site" {
  bucket = local.frontend_bucket_name

  tags = {
    Name = "chess-warehouse-static-site"
  }
}

resource "aws_s3_bucket_public_access_block" "static_site" {
  bucket = aws_s3_bucket.static_site.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "site_oac" {
  name                              = "chess-warehouse-static-site-oac"
  description                       = "CloudFront origin access control for the static site bucket"
  origin_access_control_origin_type = "s3"
  signing_protocol                  = "sigv4"
  signing_behavior                  = "always"
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.static_site.bucket_regional_domain_name
    origin_id                = "static-site-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.site_oac.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "static-site-origin"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }
  }

  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "static_site" {
  bucket = aws_s3_bucket.static_site.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontGetObject"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.static_site.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn"     = aws_cloudfront_distribution.site.arn
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

# Data source: current AWS account ID (used in ARN construction)
data "aws_caller_identity" "current" {}

# SNS topic to receive ingestion failure notifications.
resource "aws_sns_topic" "ingestion_failures" {
  name = "chess-warehouse-ingestion-failures"
}

# Optional SNS subscription for email alerts on ingestion failures.
resource "aws_sns_topic_subscription" "ingestion_failure_email" {
  count     = var.failure_alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.ingestion_failures.arn
  protocol  = "email"
  endpoint  = var.failure_alert_email
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
  function_name          = aws_lambda_function.ingestion.function_name
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


