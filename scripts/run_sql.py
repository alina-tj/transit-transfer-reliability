"""
Run a .sql file against the project database and print the results of any
SELECT statements. Avoids needing the sqlite3 command-line tool.

Usage:
    python scripts/run_sql.py sql/01_transfer_candidates.sql --db data/ets.db
"""

import argparse
import sqlite3
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlfile")
    ap.add_argument("--db", default="data/ets.db")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    buffer = ""
    for line in Path(args.sqlfile).read_text().splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            stmt = buffer.strip()
            buffer = ""
            if not stmt or all(l.strip().startswith("--") or not l.strip() for l in stmt.splitlines()):
                continue
            t0 = time.time()
            cur = conn.execute(stmt)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                print("\n" + " | ".join(cols))
                print("-" * 60)
                for r in rows:
                    print(" | ".join("" if v is None else str(v) for v in r))
            else:
                label = next(
                    (l.strip() for l in stmt.splitlines() if l.strip() and not l.strip().startswith("--")),
                    stmt.splitlines()[0],
                )
                print(f"ok ({time.time() - t0:.1f}s): {label[:70]}")
            conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
