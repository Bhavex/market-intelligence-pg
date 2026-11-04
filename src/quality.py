import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

log = logging.getLogger("market.quality")

RULE_VERSION = "quality-v2"
PROVISIONAL = "provisional"
SUSPECT = "suspect"
ACCEPTED = "accepted"
REJECTED = "rejected"
STATES = (PROVISIONAL, SUSPECT, ACCEPTED, REJECTED)
EXCLUDED_FOR_RESEARCH = (SUSPECT, REJECTED)

IST = timezone(timedelta(hours=5, minutes=30))
FINAL_AFTER = time(16, 30)


@dataclass(frozen=True)
class QualityConfig:
    jump_threshold: float = 0.20
    confirm_after: timedelta = timedelta(days=1)
    reconfirm_tolerance: float = 0.0005
    max_session_gap: timedelta = timedelta(days=10)


@dataclass(frozen=True)
class Transition:
    ticker: str
    price_date: date
    state: str
    reason: str
    evidence: dict
    close: float | None = None
    adj_close: float | None = None


def session_final_at(d: date) -> datetime:
    return datetime.combine(d, FINAL_AFTER, tzinfo=IST)


def _px(close, adj):
    return float(adj if adj is not None else close)


def ingest_states(ticker: str, result) -> list:
    suspect = {r[1] for r in result.suspect}
    out = []
    for r in result.valid:
        st = SUSPECT if r[1] in suspect else PROVISIONAL
        reason = "jump_vs_reference" if st == SUSPECT else "new_observation"
        out.append(Transition(ticker, r[1], st, reason, {}, float(r[5]), None if r[6] is None else float(r[6])))
    for i in result.rejected:
        if i.price_date is None:
            continue
        row = i.row or ()
        close = float(row[5]) if len(row) > 5 and row[5] is not None else None
        out.append(Transition(ticker, i.price_date, REJECTED, i.check, {"detail": i.detail}, close, None))
    return out


def decide_transitions(ticker: str, series: list, now: datetime, refetched: dict | None = None,
                       config: QualityConfig = QualityConfig()) -> list:
    refetched = refetched or {}
    rows = [dict(price_date=d, close=c, adj_close=a, state=s) for d, c, a, s in sorted(series, key=lambda x: x[0])]
    out = []
    ref = None
    thr = config.jump_threshold
    for i, row in enumerate(rows):
        px = _px(row["close"], row["adj_close"])
        state = row["state"]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if nxt is not None and nxt["price_date"] - row["price_date"] > config.max_session_gap:
            nxt = None
        new, reason, evidence = None, None, {}
        if state == SUSPECT and ref is not None:
            move = px / ref - 1
            if abs(move) <= thr:
                new, reason, evidence = PROVISIONAL, "reference_confirmed", {"move_vs_reference": round(move, 6)}
            elif nxt is not None:
                npx = _px(nxt["close"], nxt["adj_close"])
                back = npx / px - 1
                if abs(back) > thr and back * move < 0 and abs(npx / ref - 1) <= thr:
                    new, reason = REJECTED, "reversed_next_session"
                else:
                    new, reason = ACCEPTED, "jump_persisted"
                evidence = {"move": round(move, 6), "next_move": round(back, 6), "next_date": str(nxt["price_date"])}
        elif state == PROVISIONAL and row["price_date"] in refetched:
            old_enough = now >= session_final_at(row["price_date"]) + config.confirm_after
            again = refetched[row["price_date"]]
            same = abs(again / px - 1) <= config.reconfirm_tolerance if px else False
            if old_enough and same:
                new, reason, evidence = ACCEPTED, "reconfirmed_by_later_fetch", {"refetched": again}
        if new is not None:
            out.append(Transition(ticker, row["price_date"], new, reason, evidence, row["close"], row["adj_close"]))
            row["state"] = new
        if row["state"] not in EXCLUDED_FOR_RESEARCH:
            ref = px
    return out


INSERT_SQL = """
INSERT INTO quality_states (ticker, price_date, state, reason, rule_version, close, adj_close, evidence, run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
"""

SERIES_SQL = """
SELECT p.price_date, p.close::float8, p.adj_close::float8, q.state
FROM daily_prices p
LEFT JOIN v_quality_current q ON q.ticker = p.ticker AND q.price_date = p.price_date
WHERE p.ticker = %s AND p.price_date BETWEEN %s AND %s
ORDER BY p.price_date
"""


def _table_exists(conn) -> bool:
    return conn.execute("SELECT to_regclass('public.quality_states')").fetchone()[0] is not None


def record(conn, transitions: list, run_id=None) -> int:
    if not transitions:
        return 0
    try:
        with conn.transaction():
            if not _table_exists(conn):
                return 0
            with conn.cursor() as cur:
                cur.executemany(INSERT_SQL, [(t.ticker, t.price_date, t.state, t.reason, RULE_VERSION, t.close,
                                              t.adj_close, json.dumps(t.evidence), run_id) for t in transitions])
        return len(transitions)
    except Exception as exc:
        log.warning("could not record %d quality state(s): %s", len(transitions), exc)
        return 0


def changed_only(conn, transitions: list) -> list:
    if not transitions:
        return []
    keys = list({(t.ticker, t.price_date) for t in transitions})
    try:
        with conn.transaction():
            if not _table_exists(conn):
                return []
            cur = conn.execute("""SELECT ticker, price_date, state, close::float8 FROM v_quality_current
                                  WHERE (ticker, price_date) IN (SELECT * FROM unnest(%s::text[], %s::date[]))""",
                               ([k[0] for k in keys], [k[1] for k in keys]))
            current = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}
    except Exception as exc:
        log.warning("could not read current quality states: %s", exc)
        return transitions
    out = []
    for t in transitions:
        cur = current.get((t.ticker, t.price_date))
        if cur is None:
            out.append(t)
            continue
        if t.state == REJECTED:
            continue
        same_value = t.close is None or cur[1] is None or abs(cur[1] - t.close) <= 1e-9
        if same_value:
            continue
        out.append(Transition(t.ticker, t.price_date, t.state, "revised_value",
                              {**t.evidence, "previous_close": cur[1], "previous_state": cur[0]}, t.close, t.adj_close))
    return out


def assess_ticker(conn, ticker: str, result, start: date, run_id=None, now: datetime | None = None,
                  config: QualityConfig = QualityConfig()) -> list:
    now = now or datetime.now(IST)
    fresh = changed_only(conn, ingest_states(ticker, result))
    record(conn, fresh, run_id)
    try:
        with conn.transaction():
            if not _table_exists(conn):
                return fresh
            last = max((r[1] for r in result.valid), default=start)
            series = conn.execute(SERIES_SQL, (ticker, start - timedelta(days=10), last)).fetchall()
    except Exception as exc:
        log.warning("could not read %s for quality reassessment: %s", ticker, exc)
        return fresh
    refetched = {r[1]: _px(r[5], r[6]) for r in result.valid}
    later = decide_transitions(ticker, series, now, refetched, config)
    record(conn, later, run_id)
    return fresh + later
