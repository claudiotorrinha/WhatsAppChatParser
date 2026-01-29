import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wcp.manifest import ManifestLogger
from wcp.models import Message
from wcp.output import write_outputs


class TestByMonthAppend(unittest.TestCase):
    def test_append_is_idempotent(self):
        messages = [
            Message(
                ts="2025-01-05T10:00:00+00:00",
                sender="A",
                type="text",
                text="hello",
                media=[],
                source_line="line-1",
            ),
            Message(
                ts="2025-01-06T11:00:00+00:00",
                sender="B",
                type="text",
                text="world",
                media=[],
                source_line="line-2",
            ),
            Message(
                ts="2025-02-01T09:00:00+00:00",
                sender="A",
                type="text",
                text="next",
                media=[],
                source_line="line-3",
            ),
        ]

        with TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            manifest = ManifestLogger(out_dir / "manifest.jsonl", enabled=False)

            write_outputs(
                messages=messages,
                folder=Path(td),
                out_dir=out_dir,
                md_max_chars=4000,
                write_md=False,
                write_by_month=True,
                me=[],
                them=[],
                manifest=manifest,
            )

            jan = (out_dir / "by-month" / "2025-01.jsonl").read_text(encoding="utf-8").splitlines()
            feb = (out_dir / "by-month" / "2025-02.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(jan), 2)
            self.assertEqual(len(feb), 1)

            # Run again; by-month files should not grow.
            write_outputs(
                messages=messages,
                folder=Path(td),
                out_dir=out_dir,
                md_max_chars=4000,
                write_md=False,
                write_by_month=True,
                me=[],
                them=[],
                manifest=manifest,
            )

            jan2 = (out_dir / "by-month" / "2025-01.jsonl").read_text(encoding="utf-8").splitlines()
            feb2 = (out_dir / "by-month" / "2025-02.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(jan2), 2)
            self.assertEqual(len(feb2), 1)


if __name__ == "__main__":
    unittest.main()
