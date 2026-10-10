"""Explicit operator command for durable WaterlooWorks Retry-After blocks.

Server Retry-After values are clamped to 24 hours and stored on the collection run
that received them. This command lists or clears those stored blocks offline. It
never contacts any service and prints only timestamps and counts.
"""

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from waterlooworks_flash_next.database import DatabaseError, open_database


def _connect(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise SystemExit("Database file not found; nothing changed.")
    try:
        return open_database(database)
    except DatabaseError as error:
        raise SystemExit(str(error)) from None


def blocked_until_values(database: Path) -> list[str]:
    connection = _connect(database)
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT blocked_until FROM collection_runs "
                "WHERE blocked_until IS NOT NULL ORDER BY blocked_until"
            )
        ]
    finally:
        connection.close()


def clear_blocks(database: Path) -> int:
    """Set blocked_until NULL on every run; returns how many rows were changed."""
    connection = _connect(database)
    try:
        with connection:
            return connection.execute(
                "UPDATE collection_runs SET blocked_until=NULL "
                "WHERE blocked_until IS NOT NULL"
            ).rowcount
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Show or clear stored WaterlooWorks Retry-After blocks. "
        "Stop the API first (or restart it afterwards): a running API keeps its "
        "cooldown in memory."
    )
    parser.add_argument("action", choices=("show", "clear"))
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.action == "show":
        values = blocked_until_values(args.database)
        for value in values:
            print(value)
        print(f"{len(values)} stored block(s).")
        return
    print(f"Cleared {clear_blocks(args.database)} stored block(s).")


if __name__ == "__main__":
    main()
