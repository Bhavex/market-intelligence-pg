import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

log = logging.getLogger("market.validation")

REJECT = "reject"
WARN = "warn"

MISSING_VALUE = "missing_value"
NON_POSITIVE_PRICE = "non_positive_price"
NEGATIVE_VOLUME = "negative_volume"
HIGH_BELOW_LOW = "high_below_low"
OUTSIDE_RANGE = "outside_range"
DUPLICATE = "duplicate_ticker_date"
PRICE_SPIKE = "price_spike"
PRICE_JUMP = "price_jump"
TRADING_DAY_GAP = "trading_day_gap"
MALFORMED = "malformed_row"


@dataclass(frozen=True)
class ValidationConfig:
    jump_threshold: float = 0.20
    max_gap_sessions: int = 3
    range_tolerance: float = 0.001


@dataclass(frozen=True)
class Issue:
    ticker: str
    price_date: date | None
    check: str
    severity: str
    detail: str
    row: tuple | None = None


@dataclass
class ValidationResult:
    valid: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected

    def counts(self) -> dict:
        out = {}
        for i in self.rejected + self.warnings:
            out[i.check] = out.get(i.check, 0) + 1
        return out

    def summary(self) -> str:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(self.counts().items()))
        return (f"{len(self.valid)} valid, {len(self.rejected)} rejected, {len(self.warnings)} warnings"
                + (f" ({parts})" if parts else ""))


def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _price(row) -> float:
    adj = row[6]
    return float(row[5] if _missing(adj) else adj)


def _row_issues(row, cfg: ValidationConfig) -> tuple[list, list]:
    ticker, day = row[0], row[1]
    o, h, l, c, adj, vol = row[2], row[3], row[4], row[5], row[6], row[7]
    rejects, warns = [], []
    missing = [n for n, v in (("open", o), ("high", h), ("low", l), ("close", c)) if _missing(v)]
    if missing:
        rejects.append(Issue(ticker, day, MISSING_VALUE, REJECT, f"missing {', '.join(missing)}", row))
        return rejects, warns
    bad = [n for n, v in (("open", o), ("high", h), ("low", l), ("close", c)) if float(v) <= 0]
    if not _missing(adj) and float(adj) <= 0:
        bad.append("adj_close")
    if bad:
        rejects.append(Issue(ticker, day, NON_POSITIVE_PRICE, REJECT, f"zero or negative {', '.join(bad)}", row))
    if not _missing(vol) and vol < 0:
        rejects.append(Issue(ticker, day, NEGATIVE_VOLUME, REJECT, f"volume {vol}", row))
    if float(h) < float(l):
        rejects.append(Issue(ticker, day, HIGH_BELOW_LOW, REJECT, f"high {h} < low {l}", row))
    if not rejects:
        lo, hi = float(l) * (1 - cfg.range_tolerance), float(h) * (1 + cfg.range_tolerance)
        outside = [n for n, v in (("open", o), ("close", c)) if not lo <= float(v) <= hi]
        if outside:
            warns.append(Issue(ticker, day, OUTSIDE_RANGE, WARN,
                               f"{', '.join(outside)} outside low-high range {l}..{h}", row))
    return rejects, warns


def missing_sessions(d0: date, d1: date, holidays: Iterable[date] = ()) -> list[date]:
    hol = set(holidays)
    out, d = [], d0 + timedelta(days=1)
    while d < d1:
        if d.weekday() < 5 and d not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def _validate_ticker(ticker, rows, previous, holidays, cfg, result):
    seen, unique = set(), []
    for r in sorted(rows, key=lambda r: r[1]):
        rejects, warns = _row_issues(r, cfg)
        if rejects:
            result.rejected.extend(rejects)
            continue
        if r[1] in seen:
            result.rejected.append(Issue(ticker, r[1], DUPLICATE, REJECT, "second row for this ticker and date", r))
            continue
        seen.add(r[1])
        result.warnings.extend(warns)
        unique.append(r)

    accepted = []
    ref = previous[1] if previous else None
    thr = cfg.jump_threshold
    for i, r in enumerate(unique):
        px = _price(r)
        if ref:
            move = px / ref - 1
            if abs(move) > thr:
                nxt = _price(unique[i + 1]) if i + 1 < len(unique) else None
                reverts = (nxt is not None and abs(nxt / px - 1) > thr
                           and (nxt / px - 1) * move < 0 and abs(nxt / ref - 1) <= thr)
                if reverts:
                    result.rejected.append(Issue(ticker, r[1], PRICE_SPIKE, REJECT,
                                                 f"{move:+.1%} then {nxt / px - 1:+.1%} next session (isolated spike)", r))
                    continue
                result.warnings.append(Issue(ticker, r[1], PRICE_JUMP, WARN,
                                             f"{move:+.1%} vs previous session (kept: not reversed)", r))
        accepted.append(r)
        ref = px

    dates = ([previous[0]] if previous else []) + [r[1] for r in accepted]
    for d0, d1 in zip(dates, dates[1:]):
        gap = missing_sessions(d0, d1, holidays)
        if len(gap) >= cfg.max_gap_sessions:
            result.warnings.append(Issue(ticker, d1, TRADING_DAY_GAP, WARN,
                                         f"{len(gap)} weekday session(s) missing between {d0} and {d1}"))
    result.valid.extend(accepted)


def validate_rows(rows, previous: dict | None = None, holidays: Iterable[date] = (),
                  config: ValidationConfig | None = None) -> ValidationResult:
    cfg = config or ValidationConfig()
    result = ValidationResult()
    groups = {}
    for r in rows:
        if not isinstance(r, (tuple, list)) or len(r) not in (8, 9) or not r[0] or not isinstance(r[1], date):
            result.rejected.append(Issue(str(r[0]) if isinstance(r, (tuple, list)) and r else "?", None,
                                         MALFORMED, REJECT, "row does not have the expected shape",
                                         tuple(r) if isinstance(r, (tuple, list)) else None))
            continue
        groups.setdefault(r[0], []).append(tuple(r))
    holidays = set(holidays)
    for ticker, group in groups.items():
        _validate_ticker(ticker, group, (previous or {}).get(ticker), holidays, cfg, result)
    result.valid.sort(key=lambda r: (r[0], r[1]))
    return result


def log_result(ticker: str, result: ValidationResult) -> None:
    for i in result.rejected:
        log.warning("%s %s REJECTED %s: %s", ticker, i.price_date, i.check, i.detail)
    for i in result.warnings:
        log.info("%s %s warn %s: %s", ticker, i.price_date, i.check, i.detail)


def load_holidays(conn) -> set:
    try:
        with conn.transaction():
            return {r[0] for r in conn.execute("SELECT holiday_date FROM market_holidays").fetchall()}
    except Exception as exc:
        log.warning("market_holidays not readable (%s); gap check uses weekdays only", exc)
        return set()


def previous_price(conn, ticker: str, before: date):
    row = conn.execute(
        """SELECT price_date, COALESCE(adj_close, close) FROM daily_prices
           WHERE ticker = %s AND price_date < %s ORDER BY price_date DESC LIMIT 1""",
        (ticker, before)).fetchone()
    return (row[0], float(row[1])) if row else None


def _json_row(row):
    if row is None:
        return None
    names = ("ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume", "source")
    return json.dumps({n: (None if _missing(v) else str(v) if isinstance(v, date) else v)
                       for n, v in zip(names, row)})


def record_rejections(conn, issues: list, run_id=None) -> int:
    if not issues:
        return 0
    try:
        with conn.transaction():
            if conn.execute("SELECT to_regclass('public.rejected_prices')").fetchone()[0] is None:
                return 0
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO rejected_prices (run_id, ticker, price_date, check_name, detail, raw_row)
                       VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
                    [(run_id, i.ticker, i.price_date, i.check, i.detail, _json_row(i.row)) for i in issues])
        return len(issues)
    except Exception as exc:
        log.warning("could not record %d rejected row(s): %s", len(issues), exc)
        return 0


@dataclass
class Screened:
    daily: list
    fallback: list
    result: ValidationResult


def screen(conn, ticker: str, daily: list, fallback: list, start: date, holidays=(), run_id=None,
           config: ValidationConfig | None = None) -> Screened:
    prev = previous_price(conn, ticker, start)
    result = validate_rows(list(daily) + list(fallback), {ticker: prev} if prev else None, holidays, config)
    log_result(ticker, result)
    record_rejections(conn, result.rejected, run_id)
    fb = {tuple(r) for r in fallback}
    return Screened([r for r in result.valid if r not in fb], [r for r in result.valid if r in fb], result)
