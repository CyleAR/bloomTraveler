"""License collection handles distribution-name spelling differences in CI."""

from email.message import Message
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import build_licenses


class BuildLicenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "notices"
        shutil.copytree(build_licenses.DESTINATION / "sources", self.destination / "sources")

    def distribution(self, name, bundled_license=False):
        metadata = Message()
        metadata["Name"] = name
        files = [Path("module.py")]
        if bundled_license:
            files.append(Path("LICENSE.txt"))
            (self.root / "LICENSE.txt").write_text("Synthetic package license", encoding="utf-8")
        return SimpleNamespace(metadata=metadata, files=files, version="1.0",
                               locate_file=lambda file: self.root / file)

    def generate(self, name, bundled_license=False):
        distribution = self.distribution(name, bundled_license)
        with patch.object(build_licenses, "distributions", return_value=[distribution]), \
             patch.object(build_licenses, "DESTINATION", self.destination):
            notices = build_licenses.generate_notices([("module", str(self.root / "module.py"), "PYMODULE")])
        return next(entry for entry in notices if entry["name"] == name)

    def test_proxy_tools_aliases_use_full_fallback_text(self):
        expected = (self.destination / "sources/proxy-tools.txt").read_text(encoding="utf-8")
        for name in ("proxy_tools", "proxy-tools", "Proxy.Tools", "PROXY__TOOLS"):
            with self.subTest(name=name):
                entry = self.generate(name)
                self.assertEqual(entry["name"], name)
                self.assertEqual(entry["license"], "BSD-3-Clause")
                self.assertEqual(entry["documents"][0]["text"], expected)
                catalog = json.loads((self.destination / "catalog.json").read_text(encoding="utf-8"))
                self.assertIn(entry, catalog["entries"])

    def test_bpylist_fallback_handles_case(self):
        self.assertEqual(self.generate("BPyList2")["documents"][0]["name"], "bpylist2.txt")

    def test_dateutil_alias_keeps_additional_notice(self):
        for name in ("python-dateutil", "python_dateutil", "Python.Dateutil"):
            with self.subTest(name=name):
                entry = self.generate(name, bundled_license=True)
                self.assertEqual([document["name"] for document in entry["documents"]],
                                 ["LICENSE.txt", "photon.txt"])

    def test_unknown_missing_license_still_fails(self):
        with self.assertRaisesRegex(ValueError, "Missing license text.*unknown_package"):
            self.generate("unknown_package")


if __name__ == "__main__":
    unittest.main()
