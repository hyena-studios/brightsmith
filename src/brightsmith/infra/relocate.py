"""Relocate Iceberg warehouses after a move, clone, or containerization.

PyIceberg writes spec-compliant **absolute** filesystem paths at write time, and
bakes the project-root prefix into four metadata layers:

  1. The SQLite catalog rows — ``metadata_location`` / ``previous_metadata_location``.
  2. Every ``*.metadata.json`` — the table ``location`` plus per-snapshot
     ``manifest-list`` paths (and the metadata/snapshot logs).
  3. Every manifest-list ``*.avro`` — references to manifest files.
  4. Every manifest ``*.avro`` — references to ``*.parquet`` data files.

When the project is moved/cloned/containerized, all four layers point at the OLD
absolute path and ``iceberg_scan`` returns **empty results instead of erroring** —
the worst failure mode for a governance product. This module is the framework's
one-command repair (generalized from futureproof-data's field-proven
``scripts/rebase_iceberg_paths.py``), deriving every root from
:mod:`brightsmith.config` rather than a hardcoded repo layout.

Usage::

    python -m brightsmith.infra.relocate --check     # read-only scan; exit 1 if stale
    python -m brightsmith.infra.relocate --apply      # rewrite to current absolute root
    python -m brightsmith.infra.relocate --relative   # rewrite to repo-root-relative

``--apply`` and ``--relative`` are idempotent (re-running is a no-op) and atomic
per file (``.tmp`` sibling + ``os.replace`` + ``fsync``); the avro codec is
preserved on round-trip.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fastavro
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    fastavro = None  # type: ignore[assignment]

from brightsmith.infra.iceberg_setup import detect_relocation

CHECK = "check"
APPLY = "apply"
RELATIVE = "relative"

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*://)(.*)$", re.IGNORECASE)


def _posix(p: Path | str) -> str:
    return str(p).replace(os.sep, "/")


@dataclass
class Plan:
    """A relocation plan derived from :mod:`brightsmith.config`."""

    project_root: str
    catalog_path: Path
    project_name: str
    warehouse_dirs: list[Path]
    markers: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls) -> Plan:
        import brightsmith.config as cfg

        root = Path(cfg.PROJECT_ROOT).resolve()
        warehouses: list[Path] = []

        def _add(p: Path) -> None:
            p = Path(p)
            if p not in warehouses:
                warehouses.append(p)

        _add(cfg.WAREHOUSE_PATH)
        _add(cfg.GOVERNANCE_WAREHOUSE)
        # Discover any other zone warehouses laid out under data/<zone>/iceberg_warehouse.
        data_dir = root / "data"
        if data_dir.is_dir():
            for wh in sorted(data_dir.glob("*/iceberg_warehouse")):
                _add(wh)

        markers: list[str] = []
        for wh in warehouses:
            try:
                rel = Path(wh).resolve().relative_to(root)
            except ValueError:
                continue
            marker = "/" + _posix(rel) + "/"
            if marker not in markers:
                markers.append(marker)

        return cls(
            project_root=_posix(root),
            catalog_path=Path(cfg.CATALOG_PATH),
            project_name=cfg.PROJECT_NAME,
            warehouse_dirs=[Path(w) for w in warehouses],
            markers=markers,
        )

    # --- path classification ---

    def _split(self, s: str) -> tuple[str, str] | None:
        """Split a path into ``(root, tail)`` where ``tail`` starts at ``data/...``.

        ``root`` is the project-root prefix WITHOUT a trailing slash (``""`` for an
        already-relative path). Returns ``None`` if ``s`` is not a warehouse path.
        Any URI scheme (``file://``) is preserved as part of ``root``.
        """
        scheme = ""
        body = s
        m = _SCHEME_RE.match(s)
        if m:
            scheme, body = m.group(1), m.group(2)
        for marker in self.markers:
            idx = body.find(marker)
            if idx != -1:
                return scheme + body[:idx], body[idx + 1 :]
            rel_marker = marker[1:]  # "data/<zone>/iceberg_warehouse/"
            if body.startswith(rel_marker):
                return scheme, body
        return None

    def is_foreign_absolute(self, s: str) -> bool:
        """True if ``s`` is an absolute warehouse path under a root other than the
        current project root (i.e. it would not resolve here)."""
        split = self._split(s)
        if split is None:
            return False
        root, _tail = split
        if root == "":
            return False  # relative — resolves against CWD
        bare = _SCHEME_RE.sub(r"\2", root)
        return bare.rstrip("/") != self.project_root.rstrip("/")

    def rebase(self, s: str, mode: str) -> str:
        """Return ``s`` rewritten for ``mode`` (APPLY → absolute, RELATIVE → relative)."""
        split = self._split(s)
        if split is None:
            return s
        root, tail = split
        if mode == RELATIVE:
            return tail  # repo-root-relative, e.g. "data/bronze/iceberg_warehouse/..."
        scheme = ""
        m = _SCHEME_RE.match(root)
        if m:
            scheme = m.group(1)
        return f"{scheme}{self.project_root}/{tail}"

    def baked_prefix(self, s: str) -> str | None:
        split = self._split(s)
        if split is None:
            return None
        root, _tail = split
        return root or None


# --- layer rewriters ---


def _swap_tree(value: Any, plan: Plan, mode: str, counter: list[int]) -> Any:
    """Recursively rebase every string in a JSON/avro-like tree."""
    if isinstance(value, str):
        new = plan.rebase(value, mode)
        if new != value:
            counter[0] += 1
        return new
    if isinstance(value, dict):
        return {k: _swap_tree(v, plan, mode, counter) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_swap_tree(v, plan, mode, counter) for v in value]
    return value


def _count_foreign_tree(value: Any, plan: Plan, counter: list[int]) -> None:
    if isinstance(value, str):
        if plan.is_foreign_absolute(value):
            counter[0] += 1
    elif isinstance(value, dict):
        for v in value.values():
            _count_foreign_tree(v, plan, counter)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _count_foreign_tree(v, plan, counter)


def _atomic_write(path: Path, write_fn) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        write_fn(f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def rebase_sqlite(plan: Plan, mode: str) -> int:
    """Rebase ``metadata_location`` / ``previous_metadata_location`` rows.

    Returns the count of values changed (CHECK mode counts foreign-absolute values
    without writing)."""
    db = plan.catalog_path
    if not db.exists():
        return 0
    con = sqlite3.connect(str(db))
    changed = 0
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='iceberg_tables'"
        )
        if cur.fetchone() is None:
            return 0
        rows = con.execute(
            "SELECT rowid, table_namespace, table_name, "
            "metadata_location, previous_metadata_location "
            "FROM iceberg_tables WHERE catalog_name = ?",
            (plan.project_name,),
        ).fetchall()
        for rowid, _ns, _name, meta_loc, prev_loc in rows:
            new_meta, new_prev = meta_loc, prev_loc
            if mode == CHECK:
                if meta_loc and plan.is_foreign_absolute(meta_loc):
                    changed += 1
                if prev_loc and plan.is_foreign_absolute(prev_loc):
                    changed += 1
                continue
            if meta_loc:
                new_meta = plan.rebase(meta_loc, mode)
            if prev_loc:
                new_prev = plan.rebase(prev_loc, mode)
            if new_meta != meta_loc or new_prev != prev_loc:
                con.execute(
                    "UPDATE iceberg_tables SET metadata_location = ?, "
                    "previous_metadata_location = ? WHERE rowid = ?",
                    (new_meta, new_prev, rowid),
                )
                changed += (new_meta != meta_loc) + (new_prev != prev_loc)
        if mode != CHECK and changed:
            con.commit()
    finally:
        con.close()
    return changed


def rebase_json(path: Path, plan: Plan, mode: str) -> int:
    """Rebase a single ``*.metadata.json``. Returns count of strings changed."""
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    counter = [0]
    if mode == CHECK:
        _count_foreign_tree(meta, plan, counter)
        return counter[0]
    new_meta = _swap_tree(meta, plan, mode, counter)
    if counter[0]:
        data = json.dumps(new_meta).encode("utf-8")
        _atomic_write(path, lambda f: f.write(data))
    return counter[0]


def rebase_avro(path: Path, plan: Plan, mode: str) -> int:
    """Rebase a single avro manifest / manifest-list. Returns count of strings changed."""
    if fastavro is None:  # pragma: no cover
        raise RuntimeError("fastavro is required for avro rebasing — run `uv sync`.")
    with open(path, "rb") as f:
        reader = fastavro.reader(f)
        schema = reader.writer_schema
        codec = reader.codec
        meta = dict(reader.metadata or {})
        records = list(reader)

    counter = [0]
    if mode == CHECK:
        for r in records:
            _count_foreign_tree(r, plan, counter)
        return counter[0]

    new_records = [_swap_tree(r, plan, mode, counter) for r in records]
    if counter[0]:
        # Preserve Iceberg's avro metadata keys (e.g. schema, content) verbatim.
        passthrough = {
            k: v for k, v in meta.items() if k not in ("avro.schema", "avro.codec")
        }

        def _write(f) -> None:
            fastavro.writer(f, schema, new_records, codec=codec, metadata=passthrough)

        _atomic_write(path, _write)
    return counter[0]


@dataclass
class ScanResult:
    sqlite_changed: int = 0
    json_files: int = 0
    json_changed: int = 0
    avro_files: int = 0
    avro_changed: int = 0
    baked_prefixes: set[str] = field(default_factory=set)

    @property
    def total_changed(self) -> int:
        return self.sqlite_changed + self.json_changed + self.avro_changed


def _collect_baked_prefixes(plan: Plan) -> set[str]:
    prefixes: set[str] = set()
    db = plan.catalog_path
    if db.exists():
        con = sqlite3.connect(str(db))
        try:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='iceberg_tables'"
            )
            if cur.fetchone() is not None:
                for (loc,) in con.execute(
                    "SELECT metadata_location FROM iceberg_tables WHERE catalog_name = ?",
                    (plan.project_name,),
                ):
                    if loc and plan.is_foreign_absolute(loc):
                        p = plan.baked_prefix(loc)
                        if p:
                            prefixes.add(p)
        except sqlite3.DatabaseError:
            pass
        finally:
            con.close()
    return prefixes


def run(mode: str, plan: Plan | None = None) -> ScanResult:
    """Execute a relocation pass in ``mode`` (CHECK / APPLY / RELATIVE)."""
    plan = plan or Plan.from_config()
    result = ScanResult()

    result.sqlite_changed = rebase_sqlite(plan, mode)
    result.baked_prefixes = _collect_baked_prefixes(plan)

    for wh in plan.warehouse_dirs:
        if not Path(wh).is_dir():
            continue
        for jp in sorted(Path(wh).rglob("*.metadata.json")):
            result.json_files += 1
            result.json_changed += rebase_json(jp, plan, mode)
        for ap in sorted(Path(wh).rglob("*.avro")):
            result.avro_files += 1
            result.avro_changed += rebase_avro(ap, plan, mode)

    return result


def assert_warehouse_not_relocated() -> None:
    """Raise :class:`WarehouseRelocationError` if the config warehouse is relocated.

    Thin wrapper used by ``run.py`` preflight and the pipeline gate so every
    enforcement path surfaces the same loud failure.
    """
    import brightsmith.config as cfg

    offending = detect_relocation(cfg.CATALOG_PATH)
    if offending:
        from brightsmith.infra.iceberg_setup import _assert_not_relocated

        _assert_not_relocated(cfg.CATALOG_PATH)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m brightsmith.infra.relocate",
        description="Detect and repair relocated Iceberg warehouses.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check",
        action="store_true",
        help="Read-only scan. Exit 1 if any foreign absolute paths are found.",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite all four layers to the current ABSOLUTE project root.",
    )
    group.add_argument(
        "--relative",
        action="store_true",
        help="Rewrite all four layers to repo-root-RELATIVE paths (for committing).",
    )
    args = parser.parse_args(argv)

    mode = CHECK if args.check else APPLY if args.apply else RELATIVE
    plan = Plan.from_config()

    if not plan.catalog_path.exists() and not any(Path(w).is_dir() for w in plan.warehouse_dirs):
        print("No Iceberg warehouse found (data/ absent). Nothing to do.")
        return 0

    print(f"Project root: {plan.project_root}")
    print(f"Catalog:      {plan.catalog_path}")
    print(f"Mode:         {mode.upper()}\n")

    result = run(mode, plan)

    if result.baked_prefixes:
        print("Foreign baked prefixes detected:")
        for p in sorted(result.baked_prefixes):
            print(f"  {p}")
        print()

    verb = "foreign" if mode == CHECK else "changed"
    print("Summary:")
    print(f"  SQLite catalog rows:  {result.sqlite_changed} {verb}")
    print(f"  metadata.json files:  {result.json_files} scanned, {result.json_changed} {verb}")
    print(f"  avro manifest files:  {result.avro_files} scanned, {result.avro_changed} {verb}")

    if mode == CHECK:
        if result.total_changed > 0:
            print(
                f"\nCHECK FAILED: {result.total_changed} artifact reference(s) carry a foreign "
                f"absolute prefix.\nRun `python -m brightsmith.infra.relocate --apply` to repair."
            )
            return 1
        print("\nCHECK OK: no foreign absolute paths.")
        return 0

    print(f"\n{mode.upper()} complete: {result.total_changed} reference(s) rewritten.")
    print("Re-run with --check to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
