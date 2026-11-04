"""F-owned report structures.

When integrated into the team's frozen shared contract, A can move these
definitions into shared/types.py and update shapecheck.py's imports.
"""

from dataclasses import dataclass, field


@dataclass
class ShapeReport:
    passed: bool | None
    expected_pixels: int
    matched_pixels: int
    missing_pixels: int
    extra_pixels: int
    recall: float
    precision: float
    mismatches: list[dict]
    message: str
    width: int = 0
    height: int = 0
    target_density: float = 0.0
    actual_density: float = 0.0
    coverage: float = 0.0
    skip_ratio: float = 0.0
    checked_cells: int = 0
    skipped_cells: int = 0
    source_hash_ok: bool | None = None
    details: list[dict] = field(default_factory=list)
    warning: str = ""
    accuracy: float = 0.0


@dataclass
class DetectionReport:
    language: str
    lines: int
    width: int
    occupied_cells: int
    total_cells: int
    density: float
    comment_characters: int
    whitespace_characters: int
    suspicious_lines: list[dict]
    limitations: str
    pattern_cells: int = 0
    pattern_density: float | None = None
    code_cells: int = 0
    code_ratio: float | None = None
    meta_available: bool = False
