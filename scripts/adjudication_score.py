import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import adjudication as A


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score two labellers against the pipeline's flags")
    ap.add_argument("--dir", type=Path, default=Path("adjudication"))
    ap.add_argument("--key", type=Path)
    ap.add_argument("--a", type=Path)
    ap.add_argument("--b", type=Path)
    ap.add_argument("--resolved", type=Path, help="optional third file that settles the disagreements")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    key = args.key or args.dir / "adjudication_key.csv"
    a, b = args.a or args.dir / "labels_A.csv", args.b or args.dir / "labels_B.csv"
    result = A.score(key, a, b, args.resolved)
    out = args.out or args.dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "adjudication_scores.json").write_text(json.dumps(result, indent=2, default=str))
    md = A.markdown(result)
    (out / "adjudication_scores.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
