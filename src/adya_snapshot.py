"""Run Adya's report queries (sql/analytics/person_b_queries.sql) and save each result as CSV,
so numbers can be compared before and after a change.

Usage:  python src/adya_snapshot.py --tag before            # -> /tmp/before_3a.csv, ...
        python src/adya_snapshot.py --tag after
        python src/adya_snapshot.py --compare before after  # prints every changed row/number
Default tasks: 3a 3b 3c 5.  Works on whatever DATABASE_URL points to (read-only queries)."""
import argparse
import csv
import re
import sys
import tempfile
from pathlib import Path

from db import get_conn

QUERY_FILE = Path(__file__).resolve().parent.parent / "sql" / "analytics" / "person_b_queries.sql"
DEFAULT_TASKS = ["3a", "3b", "3c", "5"]
OUT_DIR = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())


def load_queries(path: Path = QUERY_FILE) -> dict[str, str]:
    """Split the file on '-- TASK <id>:' headers and drop comment lines."""
    queries = {}
    for chunk in re.split(r"^-- TASK ", path.read_text(), flags=re.M)[1:]:
        task_id = chunk.split(":", 1)[0].strip()
        body = chunk.splitlines()[1:]                       # first line is the rest of the header
        sql = "\n".join(l for l in body if not l.lstrip().startswith("--")).strip()
        queries[task_id] = sql
    return queries


def csv_path(tag: str, task: str) -> Path:
    return OUT_DIR / f"{tag}_{task}.csv"


def snapshot(tag: str, tasks: list[str]):
    queries = load_queries()
    with get_conn() as conn:
        for task in tasks:
            cur = conn.execute(queries[task])
            rows = cur.fetchall()
            with open(csv_path(tag, task), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([c.name for c in cur.description])
                w.writerows(rows)
            print(f"task {task}: {len(rows)} rows -> {csv_path(tag, task)}")


def read_csv(path: Path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


def compare(a: str, b: str, tasks: list[str]) -> int:
    """Print every row/cell that differs between two snapshots. Returns the number of differences."""
    total = 0
    for task in tasks:
        ra, rb = read_csv(csv_path(a, task)), read_csv(csv_path(b, task))
        if ra[0] != rb[0]:
            print(f"task {task}: COLUMNS CHANGED {ra[0]} -> {rb[0]}")
            total += 1
            continue
        header, ra, rb = ra[0], ra[1:], rb[1:]
        diffs = []
        for i in range(max(len(ra), len(rb))):
            x = ra[i] if i < len(ra) else None
            y = rb[i] if i < len(rb) else None
            if x == y:
                continue
            if x is None or y is None:
                diffs.append(f"  row {i + 1}: {'only in ' + (b if x is None else a)}: {y or x}")
                continue
            cells = []
            for h, p, q in zip(header, x, y):
                if p != q:
                    try:
                        cells.append(f"{h}: {p} -> {q} (delta {float(q) - float(p):+.4f})")
                    except ValueError:
                        cells.append(f"{h}: {p} -> {q}")
            diffs.append(f"  row {i + 1} [{x[0]} {x[1] if len(x) > 1 else ''}]: " + "; ".join(cells))
        print(f"task {task}: {'IDENTICAL' if not diffs else str(len(diffs)) + ' row(s) differ'} ({len(rb)} rows)")
        for d in diffs:
            print(d)
        total += len(diffs)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="write /tmp/<tag>_<task>.csv")
    ap.add_argument("--compare", nargs=2, metavar=("TAG_A", "TAG_B"))
    ap.add_argument("--tasks", nargs="*", default=DEFAULT_TASKS)
    args = ap.parse_args()
    if args.compare:
        sys.exit(1 if compare(*args.compare, args.tasks) else 0)
    if not args.tag:
        ap.error("give --tag or --compare")
    snapshot(args.tag, args.tasks)


if __name__ == "__main__":
    main()
