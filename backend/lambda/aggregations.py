from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Record:
    color: str
    result: str
    speed: str
    eco: Optional[str]
    openingName: Optional[str]
    ourRating: Optional[int]
    lastMoveAt: Optional[int]


def _safe_float(value: float) -> float:
    return round(value, 3)


def _count_result(records: Iterable[Record]) -> Tuple[int, int, int, int]:
    games = wins = losses = draws = 0
    for record in records:
        games += 1
        if record.result == "WIN":
            wins += 1
        elif record.result == "LOSS":
            losses += 1
        elif record.result == "DRAW":
            draws += 1
    return games, wins, losses, draws


def _make_summary(games: int, wins: int, losses: int, draws: int) -> Dict[str, Any]:
    win_rate = 0.0 if games == 0 else wins / games
    return {
        "games": games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "winRate": _safe_float(win_rate),
    }


def _record_from_dict(record: Dict[str, Any]) -> Record:
    return Record(
        color=record.get("color", ""),
        result=record.get("result", ""),
        speed=record.get("speed", ""),
        eco=record.get("eco"),
        openingName=record.get("openingName"),
        ourRating=record.get("ourRating"),
        lastMoveAt=record.get("lastMoveAt"),
    )


def summarize_overall_stats(normalized_games: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Overall W/L/D and win rate from normalized game records."""
    records = [_record_from_dict(game) for game in normalized_games]
    games, wins, losses, draws = _count_result(records)
    return _make_summary(games, wins, losses, draws)


def summarize_by_color(normalized_games: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate stats by color (white / black)."""
    buckets: Dict[str, List[Record]] = {"WHITE": [], "BLACK": []}
    for game in normalized_games:
        record = _record_from_dict(game)
        if record.color in buckets:
            buckets[record.color].append(record)
    return {
        "white": _make_summary(*_count_result(buckets["WHITE"])),
        "black": _make_summary(*_count_result(buckets["BLACK"])),
    }


def summarize_by_time_control(normalized_games: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate games and win rate by speed/time-control category."""
    buckets: Dict[str, List[Record]] = defaultdict(list)
    for game in normalized_games:
        record = _record_from_dict(game)
        if record.speed:
            buckets[record.speed].append(record)
    result: Dict[str, Any] = {}
    for speed, records in buckets.items():
        games, wins, losses, draws = _count_result(records)
        result[speed] = {"games": games, "winRate": _safe_float(0.0 if games == 0 else wins / games)}
    return result


def summarize_by_opening(normalized_games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate per-opening play counts and results.

    Returns a sorted list of openings by descending game count.
    Each entry includes eco and openingName when available.
    """
    buckets: Dict[Tuple[Optional[str], Optional[str]], List[Record]] = defaultdict(list)
    for game in normalized_games:
        record = _record_from_dict(game)
        key = (record.eco, record.openingName)
        buckets[key].append(record)

    openings: List[Dict[str, Any]] = []
    for (eco, opening_name), records in buckets.items():
        games, wins, losses, draws = _count_result(records)
        openings.append(
            {
                "eco": eco,
                "openingName": opening_name,
                "games": games,
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "winRate": _safe_float(0.0 if games == 0 else wins / games),
            }
        )

    openings.sort(key=lambda item: (-item["games"], item["openingName"] or "", item["eco"] or ""))
    return openings


def rating_series_by_time_control(normalized_games: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Return per-time-control rating series sorted by lastMoveAt ascending."""
    buckets: Dict[str, List[Record]] = defaultdict(list)
    for game in normalized_games:
        record = _record_from_dict(game)
        if record.speed and record.ourRating is not None and record.lastMoveAt is not None:
            buckets[record.speed].append(record)

    series: Dict[str, List[Dict[str, Any]]] = {}
    for speed, records in buckets.items():
        sorted_records = sorted(records, key=lambda record: record.lastMoveAt)
        series[speed] = [
            {
                "lastMoveAt": int(record.lastMoveAt),
                "rating": int(record.ourRating),
            }
            for record in sorted_records
        ]
    return series
