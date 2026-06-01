resource "aws_s3_bucket" "raw_games" {
  bucket = var.bucket_name
}

# PK: USER#<username> (all games for one user per partition)
# SK: GAME#<lastMoveAt_ms>#<gameId> (games) or META#SYNC (cursor)
resource "aws_dynamodb_table" "chess_games" {
  name           = "chess-games"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "pk"
  range_key      = "sk"

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

