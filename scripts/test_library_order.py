"""Saved-route and place ordering persists without changing item contents."""

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


class LibraryOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "library.json"
        self.library = Library(self.path)
        for name in reversed(("A", "B", "C", "D")):
            self.library.save("routes", {"name": name, "points": [{"lat": 0, "lon": 0}]})
            self.library.save("places", {"name": name, "lat": 0, "lon": 0})
        self.library.save_map_style("bloom")

    def test_move_each_kind_before_after_and_restart(self):
        for kind in ("routes", "places"):
            with self.subTest(kind=kind):
                before = self.library.snapshot()
                a, b, c, d = before[kind]
                result = self.library.move(kind, a["id"], c["id"], "after")
                self.assertEqual(result[kind], [b, c, a, d])
                self.assertEqual(Library(self.path).snapshot(), result)
                self.assertEqual(result["places" if kind == "routes" else "routes"],
                                 before["places" if kind == "routes" else "routes"])
                self.assertEqual(result["preferences"], before["preferences"])
                self.assertEqual(self.library.move(kind, a["id"], b["id"], "before")[kind],
                                 [a, b, c, d])

    def test_renaming_and_overwriting_keep_position(self):
        for kind in ("routes", "places"):
            with self.subTest(kind=kind):
                a, b, c, d = self.library.snapshot()[kind]
                self.library.move(kind, a["id"], c["id"], "after")
                self.library.save(kind, {**a, "name": "Renamed"}, a["id"])
                items = Library(self.path).snapshot()[kind]
                self.assertEqual([item["id"] for item in items], [b["id"], c["id"], a["id"], d["id"]])
                self.assertEqual(items[2]["name"], "Renamed")

    def test_invalid_requests_and_self_drop_do_not_write(self):
        before = self.library.snapshot()
        a, b, *_ = before["places"]
        for kind, identifier, target, position in (
                ("other", a["id"], b["id"], "after"),
                ("places", "missing", b["id"], "after"),
                ("places", a["id"], "missing", "before"),
                ("places", a["id"], b["id"], "invalid")):
            with self.subTest(kind=kind, identifier=identifier, target=target, position=position):
                with patch.object(self.library, "_write") as write:
                    with self.assertRaises(ValueError):
                        self.library.move(kind, identifier, target, position)
                    write.assert_not_called()
                self.assertEqual(self.library.snapshot(), before)
        with patch.object(self.library, "_write") as write:
            self.assertEqual(self.library.move("places", a["id"], a["id"], "after"), before)
            self.library.move("places", a["id"], b["id"], "before")
            write.assert_not_called()

    def test_write_failure_leaves_memory_unchanged(self):
        before = self.library.snapshot()
        a, _, c, _ = before["routes"]
        with patch.object(self.library, "_write", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.library.move("routes", a["id"], c["id"], "after")
        self.assertEqual(self.library.snapshot(), before)
        self.assertEqual(Library(self.path).snapshot(), before)

    def test_api_requires_token_and_persists_move(self):
        server = AppServer(SimpleNamespace(library=self.library), None,
                           Path(__file__).resolve().parent.parent / "web")
        server.start()
        try:
            a, b, c, d = self.library.snapshot()["places"]
            payload = json.dumps({"kind": "places", "id": a["id"], "target_id": c["id"],
                                  "position": "after"}).encode()
            request = Request(server.url + "/api/library/move", payload,
                              {"Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(self.library.snapshot()["places"], [a, b, c, d])
            request.add_header("X-Bloom-Token", server.token)
            with urlopen(request, timeout=3) as response:
                self.assertEqual(json.load(response)["places"], [b, c, a, d])
            self.assertEqual(Library(self.path).snapshot()["places"], [b, c, a, d])
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
