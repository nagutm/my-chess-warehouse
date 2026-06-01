output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "raw_games_bucket" {
  description = "S3 bucket for raw NDJSON game data"
  value       = aws_s3_bucket.raw_games.id
}

output "chess_games_table" {
  description = "DynamoDB table for normalized games"
  value       = aws_dynamodb_table.chess_games.name
}

output "lichess_token_parameter" {
  description = "SSM Parameter Store path for Lichess token"
  value       = aws_ssm_parameter.lichess_token.name
}