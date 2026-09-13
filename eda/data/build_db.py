"""Build the read-only business database.

    python -m eda.data.build_db --dataset demo
    python -m eda.data.build_db --dataset fixture --db data\\fixture.db

Overwrite policy (deliberately annoying)
----------------------------------------
If the target file already exists the script refuses and exits with code 2.
Nothing is deleted. Passing ``--force-overwrite`` is the only way to replace an
existing database, and even then the new database is built into a temporary file
first and only swapped in once it has been fully verified -- a failed rebuild
never leaves you without a working database.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from eda.config import get_settings
from eda.data.fixture_dataset import FIXTURE_NAME, load_fixture_dataset
from eda.data.generator import DEMO_NAME, DemoDataSpec, generate_demo_dataset
from eda.data.loader import write_dataset
from eda.db import DatabaseError, require_supported_sqlite
from eda.domain.models import Dataset

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TARGET_EXISTS = 2

_TMP_SUFFIX = ".tmp-build"


class TargetExistsError(RuntimeError):
    """Raised when the target database exists and overwriting was not requested."""


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m eda.data.build_db",
        description="Create the fictional e-commerce SQLite database.",
    )
    parser.add_argument(
        "--dataset",
        choices=(DEMO_NAME, FIXTURE_NAME),
        default=DEMO_NAME,
        help="'demo' = seeded multi-month data, 'fixture' = tiny hand-checkable data",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=f"target sqlite file (default: {settings.business_db_path} for demo, "
        f"{settings.fixture_db_path} for fixture)",
    )
    parser.add_argument(
        "--force-overwrite",
        action="store_true",
        help="replace an existing database; without this flag an existing file is kept",
    )
    parser.add_argument("--seed", type=int, default=settings.demo_seed)
    parser.add_argument("--start-date", default=settings.demo_start_date)
    parser.add_argument("--end-date", default=settings.demo_end_date)
    parser.add_argument("--customers", type=int, default=None)
    parser.add_argument("--orders", type=int, default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser


def _default_db_path(dataset: str) -> Path:
    settings = get_settings()
    return settings.business_db_path if dataset == DEMO_NAME else settings.fixture_db_path


def build_dataset(args: argparse.Namespace) -> Dataset:
    if args.dataset == FIXTURE_NAME:
        return load_fixture_dataset()
    spec_kwargs: dict[str, object] = {
        "seed": args.seed,
        "start_date": args.start_date,
        "end_date": args.end_date,
    }
    if args.customers is not None:
        spec_kwargs["n_customers"] = args.customers
    if args.orders is not None:
        spec_kwargs["n_orders"] = args.orders
    return generate_demo_dataset(DemoDataSpec(**spec_kwargs))


def build_database(
    dataset: Dataset, db_path: Path, *, force_overwrite: bool = False
) -> dict[str, int]:
    """Create ``db_path`` from ``dataset``, honouring the overwrite policy."""
    require_supported_sqlite()
    db_path = Path(db_path)
    if db_path.exists() and not force_overwrite:
        raise TargetExistsError(
            f"target database already exists: {db_path}\n"
            "Refusing to touch it. Either pick another path with --db, delete the "
            "file yourself, or re-run with --force-overwrite."
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    staging = db_path.with_name(db_path.name + _TMP_SUFFIX)
    for leftover in (staging, Path(str(staging) + "-journal"), Path(str(staging) + "-wal")):
        if leftover.exists():
            leftover.unlink()

    try:
        counts = write_dataset(dataset, staging)
        os.replace(staging, db_path)  # atomic on Windows and POSIX
    except BaseException:
        if staging.exists():
            staging.unlink()
        raise
    return counts


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = args.db or _default_db_path(args.dataset)

    try:
        dataset = build_dataset(args)
        counts = build_database(dataset, db_path, force_overwrite=args.force_overwrite)
    except TargetExistsError as exc:
        print(f"[build_db] {exc}", file=sys.stderr)
        return EXIT_TARGET_EXISTS
    except (DatabaseError, ValueError) as exc:
        print(f"[build_db] build failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not args.quiet:
        print(f"[build_db] dataset={dataset.name} -> {db_path}")
        for table, count in counts.items():
            print(f"[build_db]   {table:<12} {count:>7} rows")
        if dataset.name == DEMO_NAME:
            print(
                f"[build_db] reproducible: seed={args.seed} "
                f"window={args.start_date}..{args.end_date}"
            )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
