"""Apply checksummed migrations to an explicitly configured database."""

import argparse
import hashlib
from pathlib import Path

import psycopg


def migrate(url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(719193731)")
        conn.execute("""CREATE TABLE IF NOT EXISTS public.adjutant_migration
                     (name text PRIMARY KEY, checksum text NOT NULL,
                      applied_at timestamptz NOT NULL DEFAULT now())""")
        for path in sorted((root / "migrations").glob("*.sql")):
            content = path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            prior = conn.execute(
                "SELECT checksum FROM public.adjutant_migration WHERE name=%s", (path.name,)
            ).fetchone()
            if prior:
                if prior[0] != checksum:
                    raise RuntimeError(f"Applied migration changed: {path.name}")
                continue
            conn.execute(content.decode("utf-8-sig"))
            conn.execute(
                "INSERT INTO public.adjutant_migration(name,checksum) VALUES(%s,%s)",
                (path.name, checksum),
            )
            print(f"Applied {path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url-file", required=True, type=Path)
    args = parser.parse_args()
    migrate(args.url_file.read_text().strip())
