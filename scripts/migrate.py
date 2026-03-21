"""Run database migrations."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    """Run PostgreSQL and Neo4j migrations."""
    print("Running PostgreSQL migrations...")
    result = subprocess.run(
        ["alembic", "-c", "migrations/pg/alembic.ini", "upgrade", "head"],
        check=False,
    )
    if result.returncode != 0:
        print("PostgreSQL migration failed!")
        sys.exit(1)

    print("Neo4j migrations: not yet implemented (Phase 1)")
    print("All migrations complete.")


if __name__ == "__main__":
    main()
