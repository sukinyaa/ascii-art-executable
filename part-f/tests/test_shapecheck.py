"""F-only tests; FakePixelGrid mirrors B's public PixelGrid contract."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from reports import ShapeReport
from shapecheck import check_shape, detect, render_report


class Cell:
    def __init__(self, x, y, fg, ch):
        self.x = x
        self.y = y
        self.fg = fg
        self.ch = ch


class FakePixelGrid:
    def __init__(self, rows, flags):
        self.height = len(rows)
        self.width = len(rows[0])
        self.cells = tuple(
            Cell(x, y, flags[y][x], rows[y][x])
            for y in range(self.height)
            for x in range(self.width)
        )

    def at(self, x, y):
        return self.cells[y * self.width + x]


class ShapeCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sample.c"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    @staticmethod
    def metadata(source, rows, fg_rows, origin=(0, 0), code_spans=None):
        return {
            "schema_version": 1,
            "grid_rows": rows,
            "fg_rows": fg_rows,
            "origin": {"line": origin[0], "column": origin[1]},
            "size": {"width": len(rows[0]), "height": len(rows)},
            "code_spans": code_spans or [],
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }

    def test_accepts_B_pixelgrid_shape_without_importing_B(self):
        grid = FakePixelGrid([" # ", "###"], [[False, True, False], [True, True, True]])
        self.write(" # \n###\n")
        report = check_shape(self.path, grid)
        self.assertIsInstance(report, ShapeReport)
        self.assertTrue(report.passed)
        self.assertEqual(report.expected_pixels, 4)

    def test_fg_flag_is_authoritative_for_space_glyph(self):
        grid = FakePixelGrid(["  "], [[True, False]])
        self.write("# \n")
        self.assertTrue(check_shape(self.path, grid).passed)
        self.write("  \n")
        self.assertFalse(check_shape(self.path, grid).passed)

    def test_grid_detects_missing_position_and_coordinates(self):
        grid = FakePixelGrid(["##"], [[True, True]])
        self.write(" #\n")
        report = check_shape(self.path, grid)
        self.assertFalse(report.passed)
        self.assertEqual((report.details[0]["x"], report.details[0]["y"]), (0, 0))
        self.assertEqual((report.details[0]["line"], report.details[0]["column"]), (0, 0))

    def test_grid_rejects_malformed_coordinate_order(self):
        grid = FakePixelGrid(["##"], [[True, True]])
        grid.cells = (Cell(1, 0, True, "#"), Cell(0, 0, True, "#"))
        self.write("##\n")
        with self.assertRaises(ValueError):
            check_shape(self.path, grid)

    def test_sidecar_and_hash(self):
        source = "##\n #\n"
        self.write(source)
        meta = self.metadata(source, ["##", " #"], ["11", "01"])
        report = check_shape(self.path, meta=meta)
        self.assertTrue(report.passed)
        self.assertTrue(report.source_hash_ok)

    def test_sidecar_code_spans_count_as_skips(self):
        source = "#x  \n"
        self.write(source)
        meta = self.metadata(source, ["# # "], ["1000"],
                             code_spans=[{"y": 0, "x_start": 1, "x_end": 2}])
        report = check_shape(self.path, meta=meta)
        self.assertTrue(report.passed)
        self.assertEqual((report.coverage, report.skip_ratio), (0.75, 0.25))
        self.assertFalse(check_shape(self.path, meta=meta, max_skip_ratio=0.2).passed)

    def test_stale_hash_fails(self):
        source = "##\n"
        self.write(source)
        meta = self.metadata(source, ["##"], ["11"])
        self.write("# \n")
        report = check_shape(self.path, meta=meta)
        self.assertFalse(report.passed)
        self.assertFalse(report.source_hash_ok)
        self.assertIn("hash mismatch", report.message)

    def test_missing_sidecar_is_skip_not_pass(self):
        self.write("#\n")
        report = check_shape(self.path)
        self.assertIsNone(report.passed)
        self.assertIn("SKIP", render_report(report))

    def test_sidecar_auto_discovery(self):
        self.write("#\n")
        meta = self.metadata("#\n", ["#"], ["1"])
        Path(f"{self.path}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.assertTrue(check_shape(self.path).passed)

    def test_sidecar_rejects_bad_schema_and_code_span(self):
        self.write("#x\n")
        meta = self.metadata("#x\n", ["##"], ["11"],
                             code_spans=[{"y": 0, "x_start": 0, "x_end": 1}])
        with self.assertRaises(ValueError):
            check_shape(self.path, meta=meta)
        meta = self.metadata("#x\n", ["##"], ["11"])
        meta["schema_version"] = 2
        with self.assertRaises(ValueError):
            check_shape(self.path, meta=meta)

    def test_shape_thresholds(self):
        self.write("###\n")
        self.assertFalse(check_shape(self.path, ["# #"]).passed)
        self.assertTrue(check_shape(self.path, ["# #"], min_accuracy=0.6).passed)

    def test_exact_mode(self):
        self.write("xy\n")
        self.assertFalse(check_shape(self.path, ["xx"], mode="exact").passed)

    def test_rejects_unsupported_coordinates(self):
        self.write("\t#\n")
        with self.assertRaises(ValueError):
            check_shape(self.path, ["#"], start_column=2)
        self.write("#\r\n")
        with self.assertRaises(ValueError):
            check_shape(self.path, ["#"])

    def test_detection_reports_stats_without_judging_comments(self):
        self.write('char *s = "/* ## */";\n/* ## */\n')
        report = detect(self.path)
        self.assertEqual(report.suspicious_lines, [])
        self.assertGreater(report.comment_characters, 0)
        self.assertIn("does not judge", report.limitations)

    def test_detection_reads_sidecar_stats(self):
        source = "/* ## */\n"
        self.write(source)
        meta = self.metadata(source, ["##"], ["11"],
                             code_spans=[{"y": 0, "x_start": 1, "x_end": 2}])
        report = detect(self.path, meta=meta)
        self.assertTrue(report.meta_available)
        self.assertEqual(report.pattern_cells, 2)
        self.assertEqual(report.code_cells, 1)

    def test_empty_detection(self):
        self.write("")
        self.assertEqual(detect(self.path).density, 0)


if __name__ == "__main__":
    unittest.main()
