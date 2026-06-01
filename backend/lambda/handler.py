"""
Lichess Ingestion Lambda Handler

This Lambda function implements the automated, incremental synchronization of chess games
from Lichess into the data warehouse. It is responsible for:
  1. Reading the sync cursor (last synced timestamp) from DynamoDB
  2. Fetching all games since that cursor from the Lichess API
  3. Storing the raw NDJSON response in S3 (source of truth)
  4. Parsing and normalizing games into DynamoDB records
  5. Advancing the sync cursor only on successful completion

Design principles:
  - Incremental: only fetches games newer than the last successful sync
  - Idempotent: re-running with overlapping windows produces no duplicates (game ID in sort key)
  - Resilient: handles 429 rate-limiting and transient errors; failed runs don't advance the cursor
  - Observable: logs key metrics (games ingested, sync timestamp) for CloudWatch monitoring

Environment variables (required):
  - DYNAMODB_TABLE: name of the DynamoDB table
  - S3_RAW_BUCKET: name of the S3 bucket for raw game storage
  - LICHESS_USERNAME: the Lichess username to fetch games for
  - SSM_PARAMETER_NAME: path to the SSM Parameter Store secret holding the Lichess API token

Invoked by: EventBridge Scheduler (nightly) and on-demand testing.
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import boto3

# AWS clients
dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")
ssm_client = boto3.client("ssm")

# Logging setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "chess-games")
S3_RAW_BUCKET = os.environ.get("S3_RAW_BUCKET", "chess-warehouse-raw")
LICHESS_USERNAME = os.environ.get("LICHESS_USERNAME", "")
SSM_PARAMETER_NAME = os.environ.get("SSM_PARAMETER_NAME", "lichess-api-token")

# Lichess API configuration
LICHESS_API_BASE = "https://lichess.org/api"
LICHESS_USER_AGENT = "chess-data-warehouse/1.0 (+https://github.com/user/chess-data-warehouse)"
RATE_LIMIT_PAUSE_SECONDS = 60  # Minimum pause on HTTP 429 per Lichess best practices
OVERLAP_BUFFER_MS = 5 * 60 * 1000  # 5-minute buffer to ensure boundary games aren't missed
BACKFILL_START_DATE = "2026-05-01T00:00:00Z"  # First-run cursor seed (configurable)


class IngestionError(Exception):
    """Raised when ingestion cannot proceed (non-retryable)."""

    pass


class RateLimitError(Exception):
    """Raised when Lichess returns 429; indicates a retry/pause is appropriate."""

    pass


def get_lichess_token() -> str:
    """
    Fetch the Lichess personal access token from AWS Systems Manager Parameter Store.

    The token is never logged or printed. NFR-5: Credentials are not in source control.

    Returns:
        str: The Lichess API token.

    Raises:
        IngestionError: If the token cannot be retrieved.
    """
    try:
        response = ssm_client.get_parameter(Name=SSM_PARAMETER_NAME, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ssm_client.exceptions.ParameterNotFound:
        raise IngestionError(
            f"Lichess API token not found in SSM Parameter Store at {SSM_PARAMETER_NAME}"
        )
    except Exception as e:
        raise IngestionError(f"Failed to retrieve Lichess token: {str(e)}")


def read_sync_cursor() -> Dict[str, Any]:
    """
    Read the sync cursor (META#SYNC) from DynamoDB.

    The cursor tracks:
      - lastSyncedAt: epoch milliseconds of the last successfully ingested game
      - lastRunAt, lastRunGameCount, lastRunStatus: metadata from the last run

    On first invocation (no cursor found), this returns a seeded cursor pointing to the
    configured backfill start date.

    Returns:
        Dict with keys: lastSyncedAt, lastRunAt, lastRunGameCount, lastRunStatus
    """
    table = dynamodb.Table(DYNAMODB_TABLE)
    try:
        response = table.get_item(Key={"pk": f"USER#{LICHESS_USERNAME}", "sk": "META#SYNC"})
        if "Item" in response:
            logger.info(f"Sync cursor found: lastSyncedAt={response['Item'].get('lastSyncedAt')}")
            return response["Item"]
    except Exception as e:
        logger.warning(f"Failed to read sync cursor: {str(e)}; using backfill start date.")

    # First run or read failed: seed to backfill start date
    start_dt = datetime.fromisoformat(BACKFILL_START_DATE.replace("Z", "+00:00"))
    start_ms = int(start_dt.timestamp() * 1000)
    logger.info(f"Seeding cursor to backfill start: {BACKFILL_START_DATE} ({start_ms} ms)")
    return {
        "pk": f"USER#{LICHESS_USERNAME}",
        "sk": "META#SYNC",
        "lastSyncedAt": start_ms,
        "lastRunAt": None,
        "lastRunGameCount": 0,
        "lastRunStatus": None,
    }


def fetch_games_from_lichess(token: str, since_ms: int) -> str:
    """
    Fetch games from Lichess for the configured user since a given timestamp.

    Implements polite API client behavior:
      - Identifies with a descriptive User-Agent (Lichess etiquette)
      - Includes opening data (required for the opening stat)
      - Handles HTTP 429 rate-limiting with a pause-and-retry
      - Uses Bearer token authentication

    Args:
        token: Lichess personal access token.
        since_ms: Epoch milliseconds; only games played after this timestamp are returned.

    Returns:
        str: NDJSON response body.

    Raises:
        RateLimitError: If Lichess returns 429 (caller should pause and retry).
        IngestionError: For other HTTP errors or network failures.
    """
    base_url = f"{LICHESS_API_BASE}/games/user/{LICHESS_USERNAME}"
    query = urllib.parse.urlencode({"since": since_ms, "opening": "true"})
    url = f"{base_url}?{query}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/x-ndjson",
        "User-Agent": LICHESS_USER_AGENT,
    }

    logger.info(f"Fetching games from Lichess since {since_ms}")

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.getcode()
            body = response.read().decode("utf-8")

            if status == 429:
                logger.error(
                    f"Rate limited by Lichess (HTTP 429). Minimum pause: {RATE_LIMIT_PAUSE_SECONDS}s"
                )
                raise RateLimitError("Rate limited; retry after pause.")
            if status == 401:
                raise IngestionError("Unauthorized: invalid or expired Lichess token.")
            if status >= 400:
                raise IngestionError(f"Lichess API error: {status} {body[:200]}")

            return body
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.error(
                f"Rate limited by Lichess (HTTP 429). Minimum pause: {RATE_LIMIT_PAUSE_SECONDS}s"
            )
            raise RateLimitError("Rate limited; retry after pause.")
        if e.code == 401:
            raise IngestionError("Unauthorized: invalid or expired Lichess token.")
        raise IngestionError(f"Lichess API error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise IngestionError(f"Network error fetching from Lichess: {e.reason}")


def write_raw_to_s3(raw_content: str) -> str:
    """
    Write the raw NDJSON response to S3 for durability and auditability.

    Raw data is the source of truth; it is never modified or deleted. The S3 path
    follows a Hive-style partition scheme by date so that a future analytics layer
    (Athena) can read it without preprocessing.

    S3 path: s3://{bucket}/raw/lichess/{username}/dt=YYYY-MM-DD/run-{epochMs}.ndjson

    Args:
        raw_content: Raw NDJSON string from Lichess (one game per line).

    Returns:
        str: The S3 object key that was written.

    Raises:
        IngestionError: If the write fails.
    """
    now = datetime.utcnow()
    date_partition = now.strftime("%Y-%m-%d")
    epoch_ms = int(now.timestamp() * 1000)
    s3_key = f"raw/lichess/{LICHESS_USERNAME}/dt={date_partition}/run-{epoch_ms}.ndjson"

    try:
        s3_client.put_object(Bucket=S3_RAW_BUCKET, Key=s3_key, Body=raw_content.encode("utf-8"))
        logger.info(f"Raw games written to S3: s3://{S3_RAW_BUCKET}/{s3_key}")
        return s3_key
    except Exception as e:
        raise IngestionError(f"Failed to write raw games to S3: {str(e)}")


def normalize_game(game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Transform a raw Lichess game object into a normalized DynamoDB record.

    Extracts the subset of fields needed for stats queries and applies consistent,
    understandable naming. Fields normalized:
      - color: 'WHITE' or 'BLACK' (our perspective, determined by which player is us)
      - result: 'WIN', 'LOSS', or 'DRAW'
      - speed: time control category (e.g. 'blitz', 'rapid', 'classical')
      - eco: opening ECO code (e.g. 'B50')
      - openingName: opening name (e.g. 'Sicilian Defense')
      - ourRating: our rating going into the game
      - oppRating: opponent's rating
      - rated: boolean (whether the game was rated)
      - variant: game variant (e.g. 'standard')
      - status: game result status (e.g. 'mate', 'outoftime')

    Args:
        game: Raw game object from Lichess.

    Returns:
        Dict suitable for DynamoDB; or None if the game cannot be normalized.

    Raises:
        Logs a warning and returns None on malformed input; does not raise.
    """
    try:
        game_id = game.get("id")
        if not game_id:
            logger.warning("Game missing ID; skipping.")
            return None

        created_at = game.get("createdAt")
        last_move_at = game.get("lastMoveAt")

        # Determine our color and result
        white_name = game.get("players", {}).get("white", {}).get("user", {}).get("name")
        black_name = game.get("players", {}).get("black", {}).get("user", {}).get("name")
        winner = game.get("winner")  # 'white', 'black', or absent (draw)

        if white_name == LICHESS_USERNAME:
            color = "WHITE"
            our_rating = game.get("players", {}).get("white", {}).get("rating")
            opp_rating = game.get("players", {}).get("black", {}).get("rating")
        elif black_name == LICHESS_USERNAME:
            color = "BLACK"
            our_rating = game.get("players", {}).get("black", {}).get("rating")
            opp_rating = game.get("players", {}).get("white", {}).get("rating")
        else:
            logger.warning(f"Game {game_id}: neither player is {LICHESS_USERNAME}; skipping.")
            return None

        if winner == color:
            result = "WIN"
        elif winner is None:
            result = "DRAW"
        else:
            result = "LOSS"

        # Normalize the record
        return {
            "pk": f"USER#{LICHESS_USERNAME}",
            "sk": f"GAME#{last_move_at}#{game_id}",
            "gameId": game_id,
            "color": color,
            "result": result,
            "speed": game.get("speed"),  # e.g. 'blitz'
            "eco": game.get("opening", {}).get("eco"),
            "openingName": game.get("opening", {}).get("name"),
            "ourRating": our_rating,
            "oppRating": opp_rating,
            "rated": game.get("rated"),
            "variant": game.get("variant"),
            "status": game.get("status"),
            "createdAt": created_at,
            "lastMoveAt": last_move_at,
        }
    except Exception as e:
        logger.warning(f"Error normalizing game {game.get('id')}: {str(e)}")
        return None


def upsert_games_to_dynamodb(normalized_games: List[Dict[str, Any]]) -> int:
    """
    Upsert normalized game records to DynamoDB.

    Uses BatchWriteItem for efficiency. Because the sort key includes the game ID,
    re-inserting the same game will overwrite the previous record (idempotent).
    This makes overlapping sync windows safe: boundary games are fetched again but
    don't create duplicates.

    Args:
        normalized_games: List of normalized game records.

    Returns:
        int: Count of games successfully upserted.

    Raises:
        IngestionError: If the batch write fails.
    """
    if not normalized_games:
        logger.info("No games to upsert.")
        return 0

    table = dynamodb.Table(DYNAMODB_TABLE)

    # DynamoDB BatchWriteItem is limited to 25 items per batch
    batch_size = 25
    total_upserted = 0

    for i in range(0, len(normalized_games), batch_size):
        batch = normalized_games[i : i + batch_size]
        try:
            with table.batch_writer(
                overwrite_by_pkeys=["pk", "sk"]
            ) as batch_writer:
                for game in batch:
                    batch_writer.put_item(Item=game)
            total_upserted += len(batch)
            logger.info(f"Upserted {len(batch)} games to DynamoDB.")
        except Exception as e:
            raise IngestionError(f"Failed to upsert games to DynamoDB: {str(e)}")

    return total_upserted


def advance_sync_cursor(last_game_ms: int, game_count: int) -> None:
    """
    Advance the sync cursor in DynamoDB only after a successful run.

    The cursor (lastSyncedAt) is the high-water mark of game timestamps ingested.
    It advances only on a fully successful run; if this function is not called,
    the next run will re-fetch the same games (safe due to idempotency).

    Also records run metadata for observability: when the run completed, how many
    games were processed, and that it succeeded.

    Args:
        last_game_ms: Epoch milliseconds of the most recent game ingested (or unchanged if zero games).
        game_count: Number of games processed in this run.

    Raises:
        IngestionError: If the cursor update fails.
    """
    table = dynamodb.Table(DYNAMODB_TABLE)

    # Advance cursor only if we ingested games; otherwise leave it as is
    new_cursor = last_game_ms if last_game_ms > 0 else None

    try:
        update_expr = (
            "SET lastRunAt = :runAt, lastRunGameCount = :gameCount, lastRunStatus = :status"
        )
        expr_values = {
            ":runAt": int(datetime.utcnow().timestamp() * 1000),
            ":gameCount": game_count,
            ":status": "success",
        }

        if new_cursor:
            update_expr += ", lastSyncedAt = :cursor"
            expr_values[":cursor"] = new_cursor

        table.update_item(
            Key={"pk": f"USER#{LICHESS_USERNAME}", "sk": "META#SYNC"},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )

        logger.info(f"Sync cursor advanced: lastSyncedAt={new_cursor}, games={game_count}")
    except Exception as e:
        raise IngestionError(f"Failed to advance sync cursor: {str(e)}")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda entry point for the ingestion function.

    High-level flow:
      1. Read the sync cursor from DynamoDB (seeding to backfill start on first run).
      2. Fetch the Lichess API token from SSM Parameter Store.
      3. Apply a small overlap buffer to the cursor (to ensure boundary games aren't missed).
      4. Call the Lichess API with "since" set to the buffered cursor.
      5. Stream the raw NDJSON response directly to S3 (source of truth).
      6. Parse each line, normalize, and accumulate in memory.
      7. Upsert normalized records to DynamoDB.
      8. Advance the sync cursor only if all above steps succeed.
      9. Log results and metrics for CloudWatch.

    Error handling:
      - If Lichess returns 429, log it and raise RateLimitError (the EventBridge DLQ
        and Lambda retry policy handle exponential backoff and eventual alerting).
      - If any other error occurs, do NOT advance the cursor. The next scheduled run
        will resume from the same point.

    Args:
        event: Lambda event (from EventBridge or manual invocation; not used in v1).
        context: Lambda context.

    Returns:
        Dict with keys: statusCode, body (JSON string).
    """
    logger.info("Ingestion Lambda invoked.")

    try:
        # Step 1: Read cursor
        cursor = read_sync_cursor()
        last_synced_ms = cursor.get("lastSyncedAt", 0)

        # Step 2: Fetch token
        token = get_lichess_token()

        # Step 3 & 4: Apply overlap buffer and fetch from Lichess
        fetch_since_ms = max(last_synced_ms - OVERLAP_BUFFER_MS, 0)
        raw_content = fetch_games_from_lichess(token, fetch_since_ms)

        # Step 5: Write raw to S3 (streaming for large payloads, though v1 is small)
        s3_key = write_raw_to_s3(raw_content)

        # Step 6: Parse, normalize, and accumulate games
        normalized_games = []
        max_game_ms = 0
        for line in raw_content.strip().split("\n"):
            if not line:
                continue
            try:
                game = json.loads(line)
                normalized = normalize_game(game)
                if normalized:
                    normalized_games.append(normalized)
                    game_ms = game.get("lastMoveAt", 0)
                    max_game_ms = max(max_game_ms, game_ms)
            except json.JSONDecodeError as e:
                logger.warning(f"Malformed JSON line (skipping): {str(e)}")

        # Step 7: Upsert to DynamoDB
        upserted_count = upsert_games_to_dynamodb(normalized_games)

        # Step 8: Advance cursor (only on success)
        advance_sync_cursor(max_game_ms, upserted_count)

        # Step 9: Log results
        logger.info(
            f"Ingestion complete: {upserted_count} games, "
            f"cursor advanced to {max_game_ms}, raw stored at {s3_key}"
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Ingestion successful",
                    "gamesIngested": upserted_count,
                    "cursorAdvancedTo": max_game_ms,
                    "s3Key": s3_key,
                }
            ),
        }

    except RateLimitError as e:
        logger.error(f"Rate limited by Lichess. Pausing and retrying on next schedule. {str(e)}")
        # Do NOT advance cursor. EventBridge DLQ and retry policy will handle the failure.
        raise

    except IngestionError as e:
        logger.error(f"Ingestion failed (non-retryable): {str(e)}")
        # Do NOT advance cursor. Next schedule will retry from the same point.
        raise

    except Exception as e:
        logger.error(f"Unexpected error during ingestion: {str(e)}")
        raise
