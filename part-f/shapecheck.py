"""F: shape checking with B's PixelGrid and heuristic layout detection."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import tokenize
from typing import Sequence

from reports import DetectionReport, ShapeReport


def _target(grid: object | Sequence[str]) -> tuple[list[str], list[list[bool]]]:
    if all(hasattr(grid, name) for name in ("width", "height", "at")):
        if grid.width <= 0 or grid.height <= 0:
            raise ValueError("PixelGrid dimensions must be positive")
        if len(grid.cells) != grid.width * grid.height:
            raise ValueError("PixelGrid cell count does not match dimensions")
        rows, mask = [], []
        for y in range(grid.height):
            chars, flags = [], []
            for x in range(grid.width):
                cell = grid.at(x, y)
                if (cell.x, cell.y) != (x, y):
                    raise ValueError("PixelGrid cells must be in row-major coordinate order")
                if len(cell.ch) != 1:
                    raise ValueError("each PixelGrid cell must contain one character")
                chars.append(cell.ch)
                flags.append(cell.fg)
            rows.append("".join(chars))
            mask.append(flags)
    else:
        if isinstance(grid, (str, bytes)):
            raise ValueError("grid must be PixelGrid or a sequence of string rows")
        rows = list(grid)
        if not rows or not all(isinstance(row, str) for row in rows):
            raise ValueError("grid must contain at least one string row")
        width = max(map(len, rows))
        if width == 0:
            raise ValueError("grid must have nonzero width")
        rows = [row.ljust(width) for row in rows]
        mask = [[not ch.isspace() for ch in row] for row in rows]
    if any(any(ord(ch) < 32 or ord(ch) > 126 for ch in row) for row in rows):
        raise ValueError("shape grid must use printable single-column ASCII")
    return rows, mask


def check_shape(
    path: str | Path,
    grid: object | Sequence[str] | None = None,
    *,
    meta: dict | None = None,
    start_line: int = 1,
    start_column: int = 1,
    mode: str = "occupancy",
    max_mismatches: int = 50,
    min_accuracy: float = 0.98,
    min_coverage: float = 0.75,
    max_skip_ratio: float = 0.25,
) -> ShapeReport:
    """Compare a source rectangle with a sidecar snapshot.

    Preferred contract: ``check_shape(path, meta=metadata)``. B's PixelGrid is
    supported through its width/height/at interface and each cell's fg flag.
    """
    warning = ""
    source_bytes = Path(path).read_bytes()
    source_hash_ok: bool | None = None
    from_meta = meta is not None
    if meta is None:
        sidecar = Path(f"{path}.meta.json")
        if not sidecar.exists():
            if grid is None:
                return ShapeReport(
                    None, 0, 0, 0, 0, 0.0, 0.0, [],
                    "Shape check skipped: sidecar metadata is missing",
                    warning="No sidecar was provided or found",
                )
        else:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
    if meta is not None:
        rows, mask, start_line, start_column, spans = _target_from_meta(meta)
        from_meta = True
        if grid is not None:
            raise ValueError("pass either meta or the transitional grid argument, not both")
        digest = meta.get("source_sha256")
        source_hash_ok = (
            hashlib.sha256(source_bytes).hexdigest() == digest
            if isinstance(digest, str) else None
        )
        if source_hash_ok is False:
            warning = "Source SHA-256 differs from sidecar; metadata may be stale"
    else:
        if grid is None:
            return ShapeReport(None, 0, 0, 0, 0, 0.0, 0.0, [],
                               "Shape check skipped: no target grid or sidecar metadata")
        rows, mask = _target(grid)
        spans = []
    if max_mismatches < 0:
        raise ValueError("max_mismatches must be non-negative")
    if from_meta and (start_line < 0 or start_column < 0):
        raise ValueError("metadata origin must be zero-based")
    if not from_meta and (start_line < 1 or start_column < 1):
        raise ValueError("legacy origin must be 1-based")
    if mode not in ("occupancy", "exact"):
        raise ValueError("mode must be 'occupancy' or 'exact'")
    if not 0.0 <= min_accuracy <= 1.0:
        raise ValueError("min_accuracy must be between 0 and 1")
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage must be between 0 and 1")
    if not 0.0 <= max_skip_ratio <= 1.0:
        raise ValueError("max_skip_ratio must be between 0 and 1")
    width = len(rows[0])
    height = len(rows)
    if mode == "exact" and any(
        mask[y][x] and ch.isspace()
        for y, row in enumerate(rows) for x, ch in enumerate(row)
    ):
        raise ValueError("exact mode cannot check an invisible foreground glyph")
    for y, x0, x1 in spans:
        if any(mask[y][x] for x in range(x0, x1)):
            raise ValueError("code_spans may only cover background cells")
    source_start_line = start_line if from_meta else start_line - 1
    source_start_column = start_column if from_meta else start_column - 1
    text = source_bytes.decode("utf-8")
    if "\r" in text:
        raise ValueError("source must use LF line endings; CR/CRLF is unsupported")
    lines = text.split("\n")
    expected = matched = extra = occupied = skipped = evaluated = 0
    foreground_matched = background_matched = 0
    mismatches: list[dict] = []
    for y, row in enumerate(rows):
        index = source_start_line + y
        source = lines[index] if index < len(lines) else ""
        if any(ord(ch) < 32 or ord(ch) > 126 for ch in source):
            raise ValueError(f"non-printable or non-ASCII source on line {index}")
        for x, target in enumerate(row):
            if any(sy == y and x0 <= x < x1 for sy, x0, x1 in spans):
                skipped += 1
                continue
            evaluated += 1
            pos = source_start_column + x
            actual = source[pos] if pos < len(source) else " "
            occupied += not actual.isspace()
            if mask[y][x]:
                expected += 1
                ok = not actual.isspace() and (mode == "occupancy" or actual == target)
                if ok:
                    matched += 1
                    foreground_matched += 1
                else:
                    if len(mismatches) < max_mismatches:
                        mismatches.append({"x": x, "y": y, "line": index,
                                           "column": pos,
                                           "expected": target if mode == "exact" else "non-space",
                                           "actual": actual})
            else:
                if actual.isspace():
                    background_matched += 1
                else:
                    extra += 1
                    if len(mismatches) < max_mismatches:
                        mismatches.append({"x": x, "y": y, "line": index,
                                           "column": pos, "expected": " ",
                                           "actual": actual})
    missing = expected - matched
    accuracy = ((foreground_matched + background_matched) / evaluated
                if evaluated else 0.0)
    precision = matched / (matched + extra) if matched + extra else 0.0
    coverage = evaluated / (width * height) if width and height else 0.0
    skip_ratio = skipped / (width * height) if width and height else 0.0
    passed = (
        expected > 0 and evaluated > 0 and accuracy >= min_accuracy
        and coverage >= min_coverage and skip_ratio <= max_skip_ratio
        and source_hash_ok is not False
    )
    if source_hash_ok is False:
        message = "Source hash mismatch; sidecar does not match this file"
    elif not evaluated:
        message = "No cells available for shape comparison"
    elif passed:
        message = "Shape thresholds passed"
    else:
        message = "Shape accuracy, coverage, or skip threshold failed"
    return ShapeReport(
        passed=passed,
        expected_pixels=expected,
        matched_pixels=matched,
        missing_pixels=missing,
        extra_pixels=extra,
        recall=matched / expected if expected else 0.0,
        precision=precision,
        mismatches=mismatches,
        message=message,
        width=width,
        height=height,
        target_density=sum(map(sum, mask)) / (width * height),
        actual_density=occupied / evaluated if evaluated else 0.0,
        coverage=coverage,
        skip_ratio=skip_ratio,
        checked_cells=evaluated,
        skipped_cells=skipped,
        source_hash_ok=source_hash_ok,
        details=mismatches,
        warning=warning,
        accuracy=accuracy,
    )


def _target_from_meta(meta: dict) -> tuple[list[str], list[list[bool]], int, int, list[tuple[int, int, int]]]:
    """Read the proposed schema-v1 sidecar; A/E must confirm it before freeze."""
    if not isinstance(meta, dict):
        raise ValueError("meta must be a JSON object")
    if meta.get("schema_version") != 1:
        raise ValueError("unsupported or missing sidecar schema_version")
    rows = meta.get("grid_rows")
    fg_rows = meta.get("fg_rows")
    if (not isinstance(rows, list) or not rows or
            not all(isinstance(row, str) for row in rows)):
        raise ValueError("meta.grid_rows must be a non-empty list of strings")
    if (not isinstance(fg_rows, list) or len(fg_rows) != len(rows) or
            not all(isinstance(row, str) for row in fg_rows)):
        raise ValueError("meta.fg_rows must match grid_rows")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows + fg_rows):
        raise ValueError("grid_rows and fg_rows must be rectangular and equal width")
    if any(ch not in "01" for row in fg_rows for ch in row):
        raise ValueError("fg_rows must contain only '0' and '1'")
    origin = meta.get("origin")
    size = meta.get("size")
    if not isinstance(origin, dict) or not isinstance(size, dict):
        raise ValueError("meta.origin and meta.size must be objects")
    line, column = origin.get("line"), origin.get("column")
    if (not isinstance(line, int) or isinstance(line, bool) or line < 0 or
            not isinstance(column, int) or isinstance(column, bool) or column < 0):
        raise ValueError("origin.line and origin.column must be non-negative integers")
    if size != {"width": width, "height": len(rows)}:
        raise ValueError("meta.size does not match grid_rows")
    spans_raw = meta.get("code_spans", [])
    if not isinstance(spans_raw, list):
        raise ValueError("meta.code_spans must be a list")
    spans = []
    for span in spans_raw:
        if not isinstance(span, dict):
            raise ValueError("each code span must be an object")
        y, x0, x1 = span.get("y"), span.get("x_start"), span.get("x_end")
        if (not all(isinstance(v, int) and not isinstance(v, bool) for v in (y, x0, x1))
                or y < 0 or y >= len(rows) or x0 < 0 or x1 <= x0 or x1 > width):
            raise ValueError("code span coordinates must be in-grid half-open ranges")
        spans.append((y, x0, x1))
    mask = [[flag == "1" for flag in row] for row in fg_rows]
    return rows, mask, line, column, spans


def _c_comments(text: str) -> list[tuple[int, str]]:
    """Extract C-family comments while skipping quoted literals."""
    result: list[tuple[int, str]] = []
    i, line, n = 0, 1, len(text)
    while i < n:
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            result.append((line, text[i:end]))
            i = end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            comment = text[i:end]
            result.append((line, comment))
            line += comment.count("\n")
            i = end
        elif text[i] in "'\"":
            quote = text[i]
            i += 1
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    line += text[i + 1] == "\n"
                    i += 2
                elif text[i] == quote:
                    i += 1
                    break
                else:
                    line += text[i] == "\n"
                    i += 1
        else:
            line += text[i] == "\n"
            i += 1
    return result


def _python_comments(text: str) -> list[tuple[int, str]]:
    result = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                result.append((token.start[0], token.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Keep the comments found before the lexical error; detect is not a parser.
        pass
    return result


def detect(
    path: str | Path,
    *,
    language: str | None = None,
    meta: dict | None = None,
) -> DetectionReport:
    """Return non-judgmental source and optional sidecar layout statistics."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if language is None:
        language = {".py": "python", ".c": "c", ".h": "c", ".cpp": "cpp",
                    ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp"}.get(path.suffix.lower())
    if language not in ("c", "cpp", "python"):
        raise ValueError("language must be c, cpp, or python")
    lines = text.splitlines()
    # Tab expansion is fixed at 8 columns for density; shape checking rejects tabs.
    visual = [line.expandtabs(8) for line in lines]
    width = max(map(len, visual), default=0)
    occupied = sum(sum(not ch.isspace() for ch in line) for line in visual)
    total = width * len(visual)
    comments = _python_comments(text) if language == "python" else _c_comments(text)
    if meta is None:
        sidecar = Path(f"{path}.meta.json")
        if sidecar.exists():
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
    pattern_cells = code_cells = 0
    pattern_density = code_ratio = None
    if meta is not None:
        rows, mask, _, _, spans = _target_from_meta(meta)
        width, height = len(rows[0]), len(rows)
        pattern_cells = sum(map(sum, mask))
        pattern_density = pattern_cells / (width * height)
        code_cells = sum(x1 - x0 for _, x0, x1 in spans)
        code_ratio = code_cells / (width * height)
    return DetectionReport(
        language, len(lines), width, occupied, total,
        occupied / total if total else 0.0,
        sum(len(comment) for _, comment in comments),
        sum(ch.isspace() for ch in text),
        [],
        ("Aggregate statistics only; this command does not judge comments as redundant "
         "or assess code style. Detect is a v2 showcase, not a verification gate."),
        pattern_cells,
        pattern_density,
        code_cells,
        code_ratio,
        meta is not None,
    )


def render_report(report: ShapeReport | DetectionReport) -> str:
    """Render a concise, plain-text report suitable for CLI and log output."""
    if isinstance(report, ShapeReport):
        state = "PASS" if report.passed is True else "SKIP" if report.passed is None else "FAIL"
        details = "\n".join(
            f"  grid ({item['x']},{item['y']}) -> source "
            f"({item['line']},{item['column']}): expected {item['expected']!r}, "
            f"got {item['actual']!r}"
            for item in report.details[:10]
        )
        return (f"Shape: {state}\n"
                f"Accuracy: {report.accuracy:.1%}; coverage: {report.coverage:.1%}; "
                f"skipped: {report.skip_ratio:.1%}\n"
                f"Matched: {report.matched_pixels}/{report.expected_pixels} "
                f"({report.recall:.1%}); extra cells: {report.extra_pixels}\n"
                f"{report.message}"
                + (f"\n{details}" if details else "")
                + (f"\nWarning: {report.warning}" if report.warning else ""))
    return (f"Detection: {report.language}\n"
            f"Density: {report.occupied_cells}/{report.total_cells} ({report.density:.1%})\n"
            f"Comment characters: {report.comment_characters}; "
            f"whitespace characters: {report.whitespace_characters}\n"
            f"Pattern cells: {report.pattern_cells}; "
            f"pattern density: {report.pattern_density if report.pattern_density is not None else 'n/a'}\n"
            f"Code spans: {report.code_cells}; "
            f"code ratio: {report.code_ratio if report.code_ratio is not None else 'n/a'}\n"
            f"Note: {report.limitations}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Shape and heuristic detection tools")
    sub = parser.add_subparsers(dest="command", required=True)
    shape = sub.add_parser("shape")
    shape.add_argument("source")
    shape.add_argument("grid", nargs="?", help="legacy UTF-8 text grid")
    shape.add_argument("--meta", help="sidecar JSON (defaults to <source>.meta.json)")
    shape.add_argument("--line", type=int, default=1, help="legacy origin line (1-based)")
    shape.add_argument("--column", type=int, default=1, help="legacy origin column (1-based)")
    shape.add_argument("--mode", choices=["occupancy", "exact"], default="occupancy")
    shape.add_argument("--min-accuracy", type=float, default=0.98)
    shape.add_argument("--min-coverage", type=float, default=0.75)
    shape.add_argument("--max-skip-ratio", type=float, default=0.25)
    detection = sub.add_parser("detect")
    detection.add_argument("source")
    detection.add_argument("--language", choices=["c", "cpp", "python"])
    detection.add_argument("--meta", help="sidecar JSON (defaults to <source>.meta.json)")
    for command in (shape, detection):
        command.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "shape":
            grid = (Path(args.grid).read_text(encoding="utf-8").splitlines()
                    if args.grid else None)
            meta = (json.loads(Path(args.meta).read_text(encoding="utf-8"))
                    if args.meta else None)
            result = check_shape(
                args.source, grid, meta=meta, start_line=args.line,
                start_column=args.column, mode=args.mode,
                min_accuracy=args.min_accuracy, min_coverage=args.min_coverage,
                max_skip_ratio=args.max_skip_ratio,
            )
        else:
            meta = (json.loads(Path(args.meta).read_text(encoding="utf-8"))
                    if args.meta else None)
            result = detect(args.source, language=args.language, meta=meta)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2) if args.json
          else render_report(result))
    return 1 if isinstance(result, ShapeReport) and result.passed is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
