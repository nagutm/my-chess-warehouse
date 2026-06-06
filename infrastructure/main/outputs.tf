output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "raw_games_bucket" {
  description = "S3 bucket for raw NDJSON game data"
  value       = aws_s3_bucket.raw_games.id
}

output "frontend_bucket_name" {
  description = "S3 bucket name for the static frontend site"
  value       = aws_s3_bucket.static_site.bucket
}

output "frontend_cloudfront_domain" {
  description = "CloudFront distribution domain for the static frontend site"
  value       = aws_cloudfront_distribution.site.domain_name
}

output "chess_games_table" {
  description = "DynamoDB table for normalized games"
  value       = aws_dynamodb_table.chess_games.name
}

output "lichess_token_parameter" {
  description = "SSM Parameter Store path for Lichess token"
  value       = aws_ssm_parameter.lichess_token.name
}

output "ingestion_lambda_function_name" {
  description = "Name of the ingestion Lambda function"
  value       = aws_lambda_function.ingestion.function_name
}

output "ingestion_lambda_arn" {
  description = "ARN of the ingestion Lambda function"
  value       = aws_lambda_function.ingestion.arn
}

output "ingestion_lambda_role_arn" {
  description = "ARN of the ingestion Lambda execution role"
  value       = aws_iam_role.ingestion_lambda.arn
}

output "stats_lambda_function_name" {
  description = "Name of the stats Lambda function"
  value       = aws_lambda_function.stats.function_name
}

output "stats_lambda_arn" {
  description = "ARN of the stats Lambda function"
  value       = aws_lambda_function.stats.arn
}

output "stats_api_endpoint" {
  description = "HTTP API endpoint for stats routes"
  value       = aws_apigatewayv2_api.stats_api.api_endpoint
}