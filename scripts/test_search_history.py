"""Recent search queries remain bounded, private to the local library, and editable."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app_server import AppServer
from library import Library


class SearchHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "library.json"
        self.library = Library(self.path)

    def test_unicode_duplicate_and_restart(self):
        self.library.remember_search(" 서울역 ")
        self.library.remember_search("東京大学")
        self.assertEqual(self.library.remember_search("서울역"), ["서울역", "東京大学"])
        self.library.remember_search("Seoul Station")
        self.library.remember_search("SEOUL STATION")
        expected = ["SEOUL STATION", "서울역", "東京大学"]
        self.assertEqual(Library(self.path).snapshot()["recent_searches"], expected)
        with patch.object(self.library, "_write") as write:
            self.assertEqual(self.library.remember_search("SEOUL STATION"), expected)
            write.assert_not_called()

    def test_twenty_query_limit(self):
        for index in range(25):
            self.library.remember_search(f"Query {index}")
        self.assertEqual(Library(self.path).snapshot()["recent_searches"],
                         [f"Query {index}" for index in reversed(range(5, 25))])

    def test_existing_history_keeps_twenty_most_recent(self):
        queries = [f"Query {index}" for index in reversed(range(25))]
        self.path.write_text(json.dumps({"version": 1, "routes": [], "places": [],
                                         "recent_searches": queries}), encoding="utf-8")
        restored = Library(self.path)
        self.assertEqual(restored.snapshot()["recent_searches"], queries[:20])
        restored.remember_search("Newest")
        self.assertEqual(Library(self.path).snapshot()["recent_searches"], ["Newest"] + queries[:19])

    def test_remove_and_clear_preserve_routes_places_preferences(self):
        self.library.save("routes", {"name": "Route", "points": [{"lat": 0, "lon": 0}]})
        self.library.save("places", {"name": "Place", "lat": 0, "lon": 0})
        self.library.save_map_style("bloom")
        before = self.library.snapshot()
        self.library.remember_search("Seoul")
        self.library.remember_search("Tokyo")
        self.assertEqual(self.library.delete_search(" SEOUL "), ["Tokyo"])
        self.assertEqual(self.library.delete_search(), [])
        self.assertEqual(Library(self.path).snapshot(), before)

    def test_legacy_library_without_history(self):
        self.path.write_text(json.dumps({"version": 1, "routes": [], "places": []}), encoding="utf-8")
        legacy = Library(self.path)
        self.assertEqual(legacy.snapshot()["recent_searches"], [])
        legacy.remember_search("서울역")
        self.assertEqual(Library(self.path).snapshot()["recent_searches"], ["서울역"])

    def test_invalid_queries_do_not_write(self):
        for query in (None, "", "  ", "x" * 201, 5, [], {}):
            with self.subTest(query=query), patch.object(self.library, "_write") as write:
                with self.assertRaises(ValueError):
                    self.library.remember_search(query)
                write.assert_not_called()

    def test_mutations_require_token(self):
        server = AppServer(SimpleNamespace(library=self.library), None,
                           Path(__file__).resolve().parent.parent / "web")
        server.start()
        try:
            def post(path, payload, authenticated=True):
                request = Request(server.url + "/api/" + path, json.dumps(payload).encode(),
                                  {"Content-Type": "application/json"})
                if authenticated:
                    request.add_header("X-Bloom-Token", server.token)
                with urlopen(request, timeout=3) as response:
                    return json.load(response)

            for path, payload in (("search-history", {"query": "서울역"}),
                                  ("search-history/delete", {"query": "서울역"}),
                                  ("search-history/clear", {})):
                with self.subTest(path=path):
                    with self.assertRaises(HTTPError) as error:
                        post(path, payload, authenticated=False)
                    self.assertEqual(error.exception.code, 403)
            self.assertEqual(post("search-history", {"query": "서울역"}), ["서울역"])
            self.assertEqual(post("search-history", {"query": "東京大学"}), ["東京大学", "서울역"])
            with self.assertRaises(HTTPError) as error:
                post("search-history/delete", {"query": None})
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(post("search-history/delete", {"query": "서울역"}), ["東京大学"])
            self.assertEqual(post("search-history/clear", {}), [])
            self.assertEqual(Library(self.path).snapshot()["recent_searches"], [])
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
