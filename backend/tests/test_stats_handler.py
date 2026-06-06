import importlib
import json
import unittest
from unittest.mock import MagicMock, patch

stats_handler = importlib.import_module("backend.lambda.stats_handler")


class StatsHandlerTest(unittest.TestCase):
    def setUp(self):
        self.mock_table = MagicMock()
        self.mock_table.query.return_value = {
            "Items": [
                {
                    "pk": "USER#testuser",
                    "sk": "GAME#1710000000000#game-1",
                    "speed": "blitz",
                    "rated": True,
                    "color": "WHITE",
                    "result": "WIN",
                    "eco": "B20",
                    "openingName": "Sicilian Defense",
                    "ourRating": 1500,
                    "lastMoveAt": 1710000000000,
                },
                {
                    "pk": "USER#testuser",
                    "sk": "GAME#1710000001000#game-2",
                    "speed": "rapid",
                    "rated": False,
                    "color": "BLACK",
                    "result": "LOSS",
                    "eco": "C50",
                    "openingName": "Italian Game",
                    "ourRating": 1520,
                    "lastMoveAt": 1710000001000,
                },
            ]
        }
        self.table_patcher = patch.object(stats_handler.dynamodb, "Table", return_value=self.mock_table)
        self.table_patcher.start()

        self.table_name_patcher = patch.object(stats_handler, "DYNAMODB_TABLE", "chess-games")
        self.username_patcher = patch.object(stats_handler, "LICHESS_USERNAME", "testuser")
        self.table_name_patcher.start()
        self.username_patcher.start()

    def tearDown(self):
        self.table_patcher.stop()
        self.table_name_patcher.stop()
        self.username_patcher.stop()

    def test_parse_parameters_with_all_values(self):
        event = {
            "queryStringParameters": {
                "from": "2026-05-01T00:00:00Z",
                "to": "2026-06-01T00:00:00Z",
                "timeControl": "blitz",
                "rated": "true",
            }
        }
        parsed = stats_handler.parse_parameters(event)
        self.assertEqual(parsed["timeControl"], "blitz")
        self.assertTrue(parsed["rated"])
        self.assertEqual(parsed["from_ms"], 1777593600000)
        self.assertEqual(parsed["to_ms"], 1780272000000)

    def test_query_normalized_games_issues_single_query(self):
        stats_handler.query_normalized_games(1714550400000, 1717138800000)
        self.mock_table.query.assert_called_once()
        called_args = self.mock_table.query.call_args.kwargs
        self.assertIn("KeyConditionExpression", called_args)

    def test_apply_filters_filters_time_control_and_rated(self):
        filtered = stats_handler.apply_filters(
            self.mock_table.query.return_value["Items"], "blitz", True
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["speed"], "blitz")
        self.assertTrue(filtered[0]["rated"])

    def test_lambda_handler_summary_route(self):
        event = {
            "path": "/stats/summary",
            "queryStringParameters": {"timeControl": "blitz", "rated": "true"},
        }
        response = stats_handler.lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertIn("totals", body)
        self.assertIn("byColor", body)
        self.assertIn("byTimeControl", body)

    def test_lambda_handler_openings_route(self):
        event = {
            "path": "/stats/openings",
            "queryStringParameters": {"timeControl": "blitz"},
        }
        response = stats_handler.lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertIn("openings", body)

    def test_lambda_handler_ratings_route(self):
        event = {
            "path": "/stats/ratings",
            "queryStringParameters": {"rated": "false"},
        }
        response = stats_handler.lambda_handler(event, {})
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertIn("ratings", body)


if __name__ == "__main__":
    unittest.main()
