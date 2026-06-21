#!/usr/bin/env python3
"""One-time migration: copy all rows from topics/archetype_topics.csv into the
Supabase `topics` table. Run AFTER applying schema.sql and setting
SUPABASE_URL / SUPABASE_SERVICE_KEY in .env. Safe to re-run — upserts by id."""
import sys
import pathlib
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from supabase_io import _require_client

CSV_PATH = pathlib.Path(__file__).parent.parent / "topics" / "archetype_topics.csv"


def main():
    db = _require_client()
    df = pd.read_csv(CSV_PATH, dtype={
        "id": int, "video_id": str, "notes": str, "status": str,
        "category": str, "topic": str, "angle": str,
    }).fillna("")

    rows = df.to_dict("records")
    print(f"Migrating {len(rows)} rows from {CSV_PATH}...")
    db.table("topics").upsert(rows).execute()
    print("Done. Verify with: select count(*) from topics; in Supabase Studio.")


if __name__ == "__main__":
    main()
