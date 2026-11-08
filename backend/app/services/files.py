"""Question data files: storage on disk and the summary students see.

A professor attaches files to a question (e.g. ``marks.csv`` for a pandas lab, a big
training set for an ML lab). They are not stored in the database: each file is kept in
``settings.data_files_dir`` under a random 32-hex-character name, read-only, so that

- uploads of any size (up to ``max_data_file_bytes``) never pass through the database,
- the sandbox can be given the file without copying it on every run (the Judge0 workers
  see the same folder read-only, judge/README.md), and
- nobody can find a file by listing the folder: it is ``rwx--x--x``, names are random.

What students see (name, size, rows, a column summary, the first rows) is computed once
here, at upload, and kept with the file's database row, so opening a 200 MB dataset in
the exam app costs nothing.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import math
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024
KEY = re.compile(r"^[0-9a-f]{32}$")
# Letters, digits, dot, dash, underscore; starts with a letter or digit. What the code
# opens must be a plain file name in the program's folder, never a path.
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

TABLE_EXTENSIONS = {".csv": ",", ".tsv": "\t"}
TEXT_PREVIEW_CHARS = 20_000
PREVIEW_ROWS = 50
PREVIEW_COLUMNS = 40
PREVIEW_CELL_CHARS = 80
MAX_COLUMNS = 200
STATS_ROWS = 200_000  # column statistics come from the first rows; rows are always all counted
EXAMPLES = 5
DISTINCT_CAP = 1000
# What pandas reads as missing by default (the common ones).
MISSING = {"", "na", "n/a", "nan", "null", "none", "-nan", "#n/a", "<na>"}


class FileTooLarge(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    key: str
    size: int
    sha256: str
    path: Path


def check_file_name(name: str, reserved: set[str]) -> str:
    """The name the student's code opens. Raises ValueError with a message for the
    professor if it cannot be used."""
    name = (name or "").strip()
    name = name.replace("\\", "/").rsplit("/", 1)[-1]  # browsers may send a path
    if not FILE_NAME.match(name):
        raise ValueError("Use a plain file name with letters, digits, '.', '-' or '_' (e.g. marks.csv).")
    if name.lower() in {r.lower() for r in reserved}:
        raise ValueError(f"'{name}' is the name of the program file itself; rename the data file.")
    return name


class FileStore:
    """Files on disk, one per random key."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            # Sandboxes may enter the folder and read a file they know the name of, but not list it.
            os.chmod(self.root, 0o711)

    def path(self, key: str) -> Path:
        if not KEY.match(key):
            raise ValueError("bad storage key")
        return self.root / key

    async def save(self, upload: Any, max_bytes: int) -> StoredFile:
        """Copy an upload (anything with ``async read(n)``) to the store, hashing it on
        the way. Raises FileTooLarge past ``max_bytes`` (nothing is kept then)."""
        self.ensure()
        key = secrets.token_hex(16)
        final = self.path(key)
        partial = self.root / f".part-{key}"
        digest = hashlib.sha256()
        size = 0
        try:
            with open(partial, "wb") as out:
                while chunk := await upload.read(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLarge
                    digest.update(chunk)
                    out.write(chunk)
            os.chmod(partial, 0o444)  # nothing may change it once stored
            os.replace(partial, final)
        except BaseException:
            with contextlib.suppress(OSError):
                os.chmod(partial, 0o644)
                partial.unlink()
            raise
        return StoredFile(key=key, size=size, sha256=digest.hexdigest(), path=final)

    def delete(self, key: str) -> None:
        with contextlib.suppress(OSError, ValueError):
            path = self.path(key)
            if os.name == "nt":  # Windows refuses to delete read-only files
                os.chmod(path, 0o644)
            path.unlink()


# ------------------------------------------------------------------------- the summary


def _cell(text: str) -> str:
    return text if len(text) <= PREVIEW_CELL_CHARS else text[: PREVIEW_CELL_CHARS - 1] + "…"


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


class _Column:
    __slots__ = ("distinct", "examples", "filled", "missing", "name", "numeric", "total", "vmax", "vmin")

    def __init__(self, name: str) -> None:
        self.name = name
        self.filled = 0
        self.missing = 0
        self.numeric = True  # until a filled value is not a number
        self.total = 0.0
        self.vmin: float | None = None
        self.vmax: float | None = None
        self.examples: list[str] = []
        self.distinct: set[str] | None = set()

    def add(self, value: str) -> None:
        if value.strip().lower() in MISSING:
            self.missing += 1
            return
        self.filled += 1
        if self.numeric:
            number = _number(value)
            if number is None:
                self.numeric = False
            else:
                self.total += number
                self.vmin = number if self.vmin is None else min(self.vmin, number)
                self.vmax = number if self.vmax is None else max(self.vmax, number)
        if self.distinct is not None:
            self.distinct.add(value)
            if len(self.distinct) > DISTINCT_CAP:
                self.distinct = None
        if len(self.examples) < EXAMPLES and value not in self.examples:
            self.examples.append(_cell(value))

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": _cell(self.name),
            "filled": self.filled,
            "missing": self.missing,
            "distinct": len(self.distinct) if self.distinct is not None else None,  # None = over 1000
        }
        if self.filled == 0:
            out["type"] = "empty"
        elif self.numeric:
            out.update(type="number", min=self.vmin, max=self.vmax, mean=round(self.total / self.filled, 6))
        else:
            out.update(type="text", examples=self.examples)
        return out


def _count_lines(path: Path) -> int:
    lines, last = 0, b"\n"
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    return lines + (0 if last == b"\n" else 1)


def _describe_table(path: Path, delimiter: str) -> dict[str, Any]:
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(65536)
        fh.seek(0)
        if delimiter == ",":  # .csv files are sometimes ';' or tab separated (Excel in some locales)
            with contextlib.suppress(csv.Error):
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        reader = csv.reader(fh, delimiter=delimiter)
        header = next(reader, None) or []
        columns = [_Column(name or f"column {i + 1}") for i, name in enumerate(header[:MAX_COLUMNS])]
        preview: list[list[str]] = []
        rows = 0
        for row in reader:
            if not row:
                continue
            rows += 1
            if rows <= PREVIEW_ROWS:
                preview.append([_cell(v) for v in row[:PREVIEW_COLUMNS]])
            if rows <= STATS_ROWS:
                for col, value in zip(columns, row, strict=False):
                    col.add(value)
    return {
        "delimiter": delimiter,
        "rows": rows,
        "column_count": len(header),
        "columns": [c.summary() for c in columns],
        "header": [_cell(h) for h in header[:PREVIEW_COLUMNS]],
        "preview": preview,
        "stats_rows": min(rows, STATS_ROWS),
    }


def describe(path: Path, name: str) -> tuple[str, dict[str, Any]]:
    """(kind, meta) for a stored file: 'table' (CSV/TSV, with a column summary and the
    first rows), 'text' (first characters and line count) or 'binary' (size only)."""
    ext = os.path.splitext(name)[1].lower()
    with open(path, "rb") as fh:
        head = fh.read(TEXT_PREVIEW_CHARS * 4)
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the end of the sample is fine; anything else is binary.
        if exc.start < len(head) - 4:
            return "binary", {}
        text = head[: exc.start].decode("utf-8-sig")
    if "\x00" in text:
        return "binary", {}
    if ext in TABLE_EXTENSIONS:
        return "table", _describe_table(path, TABLE_EXTENSIONS[ext])
    return "text", {"lines": _count_lines(path), "preview": text[:TEXT_PREVIEW_CHARS]}


def read_code_hint(name: str, kind: str) -> str:
    """How to open the file, shown under it in the exam app."""
    if kind == "table":
        sep = "" if name.lower().endswith(".csv") else ', sep="\\t"'
        return f'pd.read_csv("{name}"{sep})'
    if kind == "text":
        return f'open("{name}").read()'
    return f'open("{name}", "rb").read()'


def csv_bytes(rows: list[list[str]]) -> bytes:
    """Small helper for tests and demo data."""
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue().encode()
