import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import adjudication as A


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sample flagged and unflagged price rows for two independent labellers")
    ap.add_argument("--out", type=Path, default=Path("adjudication"))
    ap.add_argument("--flagged", type=int, default=200)
    ap.add_argument("--unflagged", type=int, default=200)
    ap.add_argument("--min-per-stratum", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--labellers", default="A,B")
    args = ap.parse_args(argv)
    import psycopg
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    with psycopg.connect(url) as conn:
        conn.read_only = True
        df = A.load_candidates(conn)
    sample = A.stratified_sample(df, args.flagged, args.unflagged, args.seed, args.min_per_stratum)
    paths = A.export(sample, args.out, tuple(args.labellers.split(",")), args.seed)
    print(f"{len(df)} candidate rows ({int(df['flagged'].sum())} flagged); sampled {len(sample)} "
          f"({int(sample['flagged'].sum())} flagged) across {sample['stratum'].nunique()} strata")
    for name, path in paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
