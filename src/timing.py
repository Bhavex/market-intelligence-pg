import logging
import time
from collections import defaultdict
from contextlib import contextmanager

log = logging.getLogger("timing")

TOTALS = defaultdict(float)
COUNTS = defaultdict(int)


@contextmanager
def stage(name, quiet=False):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        TOTALS[name] += dt
        COUNTS[name] += 1
        if not quiet:
            print(f"TIMING {name}: {dt:.2f}s", flush=True)


def report(title="stage totals"):
    print(f"TIMING {title}:", flush=True)
    for name, total in sorted(TOTALS.items(), key=lambda kv: -kv[1]):
        print(f"TIMING   {name:28} {total:8.2f}s  x{COUNTS[name]}", flush=True)


def reset():
    TOTALS.clear()
    COUNTS.clear()
