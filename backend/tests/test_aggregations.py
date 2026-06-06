import importlib
import unittest

aggregations = importlib.import_module("backend.lambda.aggregations")


class AggregationsTest(unittest.TestCase):
    def setUp(self):
        self.games = [
            {
                "color": "WHITE",
                "result": "WIN",
                "speed": "blitz",
                "eco": "B20",
                "openingName": "Sicilian Defense",
                "ourRating": 1500,
                "lastMoveAt": 1,
            },
            {
                "color": "BLACK",
                "result": "LOSS",
                "speed": "blitz",
                "eco": "B20",
                "openingName": "Sicilian Defense",
                "ourRating": 1490,
                "lastMoveAt": 2,
            },
            {
                "color": "WHITE",
                "result": "DRAW",
                "speed": "rapid",
                "eco": "C50",
                "openingName": "Italian Game",
                "ourRating": 1510,
                "lastMoveAt": 3,
            },
            {
                "color": "BLACK",
                "result": "WIN",
                "speed": "rapid",
                "eco": "C50",
                "openingName": "Italian Game",
                "ourRating": 1505,
                "lastMoveAt": 4,
            },
        ]

    def test_summarize_overall_stats(self):
        summary = aggregations.summarize_overall_stats(self.games)
        self.assertEqual(summary, {"games": 4, "wins": 2, "losses": 1, "draws": 1, "winRate": 0.5})

    def test_summarize_by_color(self):
        by_color = aggregations.summarize_by_color(self.games)
        self.assertEqual(by_color["white"], {"games": 2, "wins": 1, "losses": 0, "draws": 1, "winRate": 0.5})
        self.assertEqual(by_color["black"], {"games": 2, "wins": 1, "losses": 1, "draws": 0, "winRate": 0.5})

    def test_summarize_by_time_control(self):
        by_time = aggregations.summarize_by_time_control(self.games)
        self.assertEqual(by_time["blitz"], {"games": 2, "winRate": 0.5})
        self.assertEqual(by_time["rapid"], {"games": 2, "winRate": 0.5})

    def test_summarize_by_opening(self):
        openings = aggregations.summarize_by_opening(self.games)
        self.assertEqual(len(openings), 2)
        names = {opening["openingName"] for opening in openings}
        self.assertEqual(names, {"Sicilian Defense", "Italian Game"})
        sicilian = next(item for item in openings if item["openingName"] == "Sicilian Defense")
        self.assertEqual(sicilian["games"], 2)
        self.assertEqual(sicilian["wins"], 1)
        self.assertEqual(sicilian["losses"], 1)
        self.assertEqual(sicilian["draws"], 0)
        self.assertEqual(sicilian["winRate"], 0.5)

    def test_rating_series_by_time_control(self):
        series = aggregations.rating_series_by_time_control(self.games)
        self.assertEqual(series["blitz"], [{"lastMoveAt": 1, "rating": 1500}, {"lastMoveAt": 2, "rating": 1490}])
        self.assertEqual(series["rapid"], [{"lastMoveAt": 3, "rating": 1510}, {"lastMoveAt": 4, "rating": 1505}])


    def test_rating_series_casts_decimal_values(self):
        from decimal import Decimal

        games = [
            {
                "color": "WHITE",
                "result": "WIN",
                "speed": "blitz",
                "eco": "B20",
                "openingName": "Sicilian Defense",
                "ourRating": Decimal("1500"),
                "lastMoveAt": Decimal("1"),
            }
        ]
        series = aggregations.rating_series_by_time_control(games)
        self.assertEqual(series["blitz"], [{"lastMoveAt": 1, "rating": 1500}])


if __name__ == "__main__":
    unittest.main()