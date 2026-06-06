from __future__ import annotations

import importlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

try:
    aggregations = importlib.import_module(f"{__package__}.aggregations")
except ModuleNotFoundError:
    aggregations = importlib.import_module("backend.lambda.aggregations")

# AWS clients
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "chess-games")
LICHESS_USERNAME = os.environ.get("LICHESS_USERNAME", "")

dynamodb = boto3.resource("dynamodb")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_EPOCH_MS = 9999999999999


def parse_iso_timestamp(value: str) -> int:
    """Parse ISO 8601 date/time into epoch milliseconds, assuming UTC for naive dates."""
    if not value:
        raise ValueError("Date string is required")

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return int(dt.timestamp() * 1000)


def parse_parameters(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse API Gateway query parameters into typed filter values."""
    params = event.get("queryStringParameters") or {}
    raw_from = params.get("from")
    raw_to = params.get("to")

    from_ms = parse_iso_timestamp(raw_from) if raw_from else 0
    to_ms = parse_iso_timestamp(raw_to) if raw_to else MAX_EPOCH_MS
    if from_ms > to_ms:
        raise ValueError("'from' must be earlier than or equal to 'to'")

    time_control = params.get("timeControl") or None
    rated_raw = params.get("rated")
    rated: Optional[bool] = None
    if rated_raw is not None:
        normalized = rated_raw.strip().lower()
        if normalized in {"true", "1", "yes"}:
            rated = True
        elif normalized in {"false", "0", "no"}:
            rated = False
        else:
            raise ValueError("rated must be true or false")

    return {
        "from_ms": from_ms,
        "to_ms": to_ms,
        "timeControl": time_control,
        "rated": rated,
        "raw_from": raw_from,
        "raw_to": raw_to,
    }


def build_sort_key_bounds(from_ms: int, to_ms: int) -> Dict[str, str]:
    return {
        "from_sk": f"GAME#{from_ms}",
        "to_sk": f"GAME#{to_ms}#\uffff",
    }


def query_normalized_games(from_ms: int, to_ms: int) -> List[Dict[str, Any]]:
    """Query DynamoDB for normalized games in a date range using the sort key."""
    table = dynamodb.Table(DYNAMODB_TABLE)
    bounds = build_sort_key_bounds(from_ms, to_ms)
    pk = f"USER#{LICHESS_USERNAME}"

    items: List[Dict[str, Any]] = []
    exclusive_start_key: Optional[Dict[str, Any]] = None

    while True:
        query_kwargs: Dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(pk)
            & Key("sk").between(bounds["from_sk"], bounds["to_sk"]),
        }
        if exclusive_start_key is not None:
            query_kwargs["ExclusiveStartKey"] = exclusive_start_key

        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break

    return items


def apply_filters(
    games: List[Dict[str, Any]], time_control: Optional[str], rated: Optional[bool]
) -> List[Dict[str, Any]]:
    """Apply in-memory `timeControl` and `rated` filters after the DynamoDB query."""
    filtered: List[Dict[str, Any]] = []
    for game in games:
        if time_control is not None and game.get("speed") != time_control:
            continue
        if rated is not None and game.get("rated") != rated:
            continue
        filtered.append(game)
    return filtered


def build_response(event: Dict[str, Any], games: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    path = event.get("rawPath") or event.get("path") or ""
    if path.endswith("/stats/summary"):
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "range": {"from": params["raw_from"], "to": params["raw_to"]},
                    "totals": aggregations.summarize_overall_stats(games),
                    "byColor": aggregations.summarize_by_color(games),
                    "byTimeControl": aggregations.summarize_by_time_control(games),
                }
            ),
        }
    if path.endswith("/stats/openings"):
        return {
            "statusCode": 200,
            "body": json.dumps({"openings": aggregations.summarize_by_opening(games)}),
        }
    if path.endswith("/stats/ratings"):
        return {
            "statusCode": 200,
            "body": json.dumps({"ratings": aggregations.rating_series_by_time_control(games)}),
        }

    return {
        "statusCode": 404,
        "body": json.dumps({"message": "Not found"}),
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """API Lambda entry point for stats queries."""
    logger.info("Stats Lambda invoked.")

    try:
        params = parse_parameters(event)
        games = query_normalized_games(params["from_ms"], params["to_ms"])
        filtered_games = apply_filters(games, params["timeControl"], params["rated"])
        return build_response(event, filtered_games, params)

    except ValueError as e:
        logger.warning(f"Bad request: {str(e)}")
        return {"statusCode": 400, "body": json.dumps({"message": str(e)})}
    except Exception as e:
        logger.error(f"Stats query failed: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"message": "Internal server error"})}
