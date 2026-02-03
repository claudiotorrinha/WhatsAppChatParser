import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wcp.config import load_config_from_argv


class TestConfig(unittest.TestCase):
    def test_load_config_from_argv(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "cfg.json"
            path.write_text(json.dumps({"tz": "+02:00"}), encoding="utf-8")
            cfg = load_config_from_argv(["prog", "--config", str(path)])
            self.assertEqual(cfg.get("tz"), "+02:00")


if __name__ == "__main__":
    unittest.main()
