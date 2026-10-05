"""One-time migration of contributor_leads and feedback from SQLite to Supabase.

Usage:
    python tools/migrate_admin_sqlite_to_supabase.py [--db-path PATH] [--dry-run]

Safety guarantees:
- Read-only SQLite access (never writes or deletes SQLite data)
- Idempotent: contributor leads use upsert on email; feedback checks for
  existing identical records before inserting
- Requires explicit CLI invocation; never runs at startup
- Fails clearly when Supabase is not configured
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# Ensure project root is on sys.path so ``backend.*`` imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.runtime_config import load_runtime_config  # noqa: E402
from backend.supabase_client import SupabaseClient, get_supabase_client  # noqa: E402

logger = logging.getLogger("migrate_admin")

DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "admin.sqlite3"


# ── SQLite readers ───────────────────────────────────────────────────────


def read_contributor_leads(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT email, source, status, notes, created_at, updated_at "
            "FROM contributor_leads ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def read_feedback(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name, message, source, created_at "
            "FROM feedback ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Migration functions ──────────────────────────────────────────────────


def migrate_contributor_leads(
    rows: list[dict],
    client: SupabaseClient,
    dry_run: bool,
) -> dict:
    stats = {"read": len(rows), "migrated": 0, "failed": 0}
    for row in rows:
        if dry_run:
            print(f"  [DRY-RUN] Would migrate lead: {row['email']}")
            stats["migrated"] += 1
            continue
        try:
            client.upsert(
                "contributor_leads",
                {
                    "email": row["email"],
                    "source": row["source"],
                    "status": row["status"],
                    "notes": row["notes"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                },
                on_conflict="email",
            )
            stats["migrated"] += 1
        except Exception as exc:
            stats["failed"] += 1
            logger.warning("Failed to migrate lead %s: %s", row["email"], exc)
    return stats


def migrate_feedback(
    rows: list[dict],
    client: SupabaseClient,
    dry_run: bool,
) -> dict:
    stats = {"read": len(rows), "migrated": 0, "skipped": 0, "failed": 0}
    for row in rows:
        if dry_run:
            print(f"  [DRY-RUN] Would migrate feedback from: {row['name'] or '(anonymous)'}")
            stats["migrated"] += 1
            continue
        try:
            existing = client.select(
                "feedback",
                {
                    "name": f"eq.{row['name']}",
                    "message": f"eq.{row['message']}",
                    "source": f"eq.{row['source']}",
                    "created_at": f"eq.{row['created_at']}",
                },
            )
            if existing:
                stats["skipped"] += 1
                continue
            client.insert(
                "feedback",
                {
                    "name": row["name"],
                    "message": row["message"],
                    "source": row["source"],
                    "created_at": row["created_at"],
                },
            )
            stats["migrated"] += 1
        except Exception as exc:
            stats["failed"] += 1
            logger.warning("Failed to migrate feedback: %s", exc)
    return stats


# ── CLI ──────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate admin SQLite data (contributor_leads, feedback) to Supabase.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read SQLite data and report what would be migrated without writing to Supabase.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    config = load_runtime_config()

    if not config.supabase_enabled:
        print("ERROR: SUPABASE_ENABLED is not true. Set it in .env and retry.")
        sys.exit(1)

    client = get_supabase_client(config)
    if client is None:
        print(
            "ERROR: Could not create Supabase client. "
            "Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env."
        )
        sys.exit(1)

    db_path = args.db_path
    if not db_path.exists():
        print(f"ERROR: SQLite database not found at {db_path}")
        sys.exit(1)

    if args.dry_run:
        print("=== DRY RUN MODE — no data will be written to Supabase ===\n")

    try:
        lead_rows = read_contributor_leads(db_path)
        feedback_rows = read_feedback(db_path)

        print(f"Contributor leads in SQLite: {len(lead_rows)}")
        print(f"Feedback rows in SQLite:     {len(feedback_rows)}\n")

        lead_stats = migrate_contributor_leads(lead_rows, client, args.dry_run)
        feedback_stats = migrate_feedback(feedback_rows, client, args.dry_run)

        print("\n=== Migration Summary ===")
        print(
            f"Contributor Leads: {lead_stats['read']} read, "
            f"{lead_stats['migrated']} migrated, "
            f"{lead_stats['failed']} failed"
        )
        print(
            f"Feedback:          {feedback_stats['read']} read, "
            f"{feedback_stats['migrated']} migrated, "
            f"{feedback_stats['skipped']} skipped (already in Supabase), "
            f"{feedback_stats['failed']} failed"
        )
        print("\nSQLite database NOT modified.")
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
