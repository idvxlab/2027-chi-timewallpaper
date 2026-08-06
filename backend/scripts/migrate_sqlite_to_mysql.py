from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sqlalchemy import MetaData, create_engine, select, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import Base
import app.db.models  # noqa: F401


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy all application rows from SQLite into an empty MySQL schema."
    )
    parser.add_argument(
        "--sqlite",
        default="./data/app.db",
        help="Path to the existing SQLite database (default: ./data/app.db).",
    )
    parser.add_argument(
        "--mysql-url",
        required=True,
        help="SQLAlchemy mysql+pymysql connection URL.",
    )
    parser.add_argument(
        "--clear-target",
        action="store_true",
        help="Delete existing application rows in MySQL before copying.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sqlite_path = Path(args.sqlite).expanduser().resolve()
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")
    if not args.mysql_url.startswith("mysql+pymysql://"):
        raise SystemExit("--mysql-url must use the mysql+pymysql:// dialect")

    source_engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
    )
    target_engine = create_engine(
        args.mysql_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

    Base.metadata.create_all(bind=target_engine)
    source_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    target_metadata = MetaData()
    target_metadata.reflect(bind=target_engine)

    application_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name in source_metadata.tables and table.name in target_metadata.tables
    ]
    copied: dict[str, int] = {}

    with source_engine.connect() as source, target_engine.begin() as target:
        if args.clear_target:
            target.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            try:
                for model_table in reversed(application_tables):
                    target.execute(target_metadata.tables[model_table.name].delete())
            finally:
                target.execute(text("SET FOREIGN_KEY_CHECKS=1"))

        for model_table in application_tables:
            name = model_table.name
            source_table = source_metadata.tables[name]
            target_table = target_metadata.tables[name]
            rows = [dict(row) for row in source.execute(select(source_table)).mappings()]
            if rows:
                target.execute(target_table.insert(), rows)
            copied[name] = len(rows)

    total = sum(copied.values())
    print(f"Copied {total} rows across {len(copied)} application tables.")
    for name, count in copied.items():
        if count:
            print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
