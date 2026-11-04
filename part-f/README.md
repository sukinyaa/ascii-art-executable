# Part F Shape Checker

This folder contains only F-owned files: shape checking, report data classes,
tests, and this integration note. It does not copy B's grid generator, images,
or shared package.

## What It Does

- Checks the generated source against a build-time grid snapshot.
- Uses B's `PixelGrid` contract (`width`, `height`, and `at(x, y)`) directly.
  Each cell's `fg` value is authoritative, including foreground cells whose
  rendered glyph is a space.
- Reads sidecar metadata, skips declared `code_spans`, checks source SHA-256,
  reports accuracy/coverage/skip ratio, and identifies mismatches by grid and
  source coordinates.
- Reports file and sidecar statistics for `detect`. It does not decide whether
  comments are redundant; `detect` is a non-blocking v2 showcase.

## Proposed Sidecar Contract

The sidecar is a proposal for integration, not a team-frozen schema. A and E
must confirm field names and semantics before this is connected to `verify`.

```json
{
  "schema_version": 1,
  "grid_rows": ["## ", " # "],
  "fg_rows": ["110", "010"],
  "origin": {"line": 1, "column": 0},
  "size": {"width": 3, "height": 2},
  "code_spans": [{"y": 0, "x_start": 1, "x_end": 2}],
  "source_sha256": "<SHA-256 of generated source bytes>"
}
```

Coordinates in metadata are zero-based. A code span uses local grid row `y`
and a half-open `[x_start, x_end)` range, and must cover background cells only.
The current suggested defaults are accuracy at least 0.98, coverage at least
0.75, and skip ratio no more than 0.25; the team must approve these values.
Missing metadata yields `passed=None` (SKIP); a stale source hash fails the
shape check.

## Use

The project integration should import `shapecheck.py` and `reports.py` from the
shared package location chosen by A. For a standalone smoke test, run:

```bash
python3 -m unittest discover -s tests -v
python3 shapecheck.py shape generated.c --meta generated.c.meta.json
python3 shapecheck.py detect generated.c --meta generated.c.meta.json --json
```

The tests use a small fake grid with the same public fields as B's `PixelGrid`;
they do not include or duplicate B's implementation. `reports.py` defines
F's report structures locally for isolated review. During integration, A can
move those structures to `shared/types.py` and update the import.

## Remaining Integration

This is the B-compatible F module, not a completed end-to-end integration.
It has not yet been connected to C/D-generated `SourceFile` artifacts or E's
`VerifyReport`. The sidecar format and thresholds are still awaiting team
approval.
