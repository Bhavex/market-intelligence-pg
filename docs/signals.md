# Signals: exact formulas, lookbacks and thresholds

Seven signals are used in the analysis. Five are computed and stored by `refresh_signals()` in
[`sql/03_signals.sql`](../sql/03_signals.sql) (table `signals`, one row per ticker per trading day). Two are **not**
stored as a column: *RSI zone* is only a chart convention, and *strongest sector* is a rank in a view. Line links point at the files as of this commit
(`bd3fe10` plus this file).

**Conventions used by every signal**
- Price `px = COALESCE(adj_close, close)` ([L22](../sql/03_signals.sql#L22)). The 52-week signals use `adj_close` only ([L98](../sql/03_signals.sql#L98)).
- A "day" is a **row** (trading session) of that ticker, not a calendar day: windows are `PARTITION BY ticker ORDER BY price_date` ([L38](../sql/03_signals.sql#L38)).
- Stored values are rounded (`return_1d`, `momentum_20d`: 6 dp; `rsi_14`: 2 dp; `volume_zscore_20d`: 4 dp, [L67-69](../sql/03_signals.sql#L67)). Flags are computed from the **unrounded** values.
- NULL inputs make a comparison NULL; where the code wraps it in `COALESCE(..., FALSE)` the flag becomes FALSE, otherwise it stays NULL (noted per signal).

## The seven signals

| # | Signal | Output | Rule | Lookback | Code |
|---|---|---|---|---|---|
| 1 | Trend label | `UP` / `DOWN` / `NEUTRAL` | `px > sma_50 > sma_200` is UP; `px < sma_50 < sma_200` is DOWN; else NEUTRAL | 50 and 200 rows | [L32-34](../sql/03_signals.sql#L32), [L54](../sql/03_signals.sql#L54), [L70-73](../sql/03_signals.sql#L70) |
| 2 | Anomaly flag | boolean (+ reason) | `abs(return_1d) > 3 * sd_prev20` **or** `volume_zscore_20d > 3` | 20 rows (excl. today) | [L35-36](../sql/03_signals.sql#L35), [L43](../sql/03_signals.sql#L43), [L58](../sql/03_signals.sql#L58), [L75-79](../sql/03_signals.sql#L75) |
| 3 | 52-week breakout | boolean | `adj_close > max(adj_close over the previous 252 rows)` | 252 rows (excl. today) | [L97-115](../sql/03_signals.sql#L97) |
| 4 | Strong breakout | boolean | breakout **and** `volume_zscore_20d > 2` | 252 rows; volume 20 rows | [L116](../sql/03_signals.sql#L116) |
| 5 | RSI zone | *not stored* | `rsi_14` is computed; zones are a chart convention at **30 / 70** | 14 rows | [L44-45](../sql/03_signals.sql#L44), [L55-57](../sql/03_signals.sql#L55), [app.js L268-269](../site/app.js#L268) |
| 6 | 20-day momentum sign | *not stored* | sign of `momentum_20d = px / px[-20] - 1` | 20 rows | [L30](../sql/03_signals.sql#L30), [L67](../sql/03_signals.sql#L67) |
| 7 | Strongest sector | sector name | sector with the highest `avg_momentum_20d` that day (`strength_rank = 1`) | 20 rows | [04_views L34](../sql/04_views.sql#L34), [L47-50](../sql/04_views.sql#L47) |

### 1. Trend label: [L70-73](../sql/03_signals.sql#L70)
```
sma_50  = avg(px, last 50 rows incl. today)           (L32)
sma_200 = avg(px, last 200 rows incl. today), NULL unless exactly 200 rows exist   (L33-34, L54)
if sma_200 IS NULL                      -> NEUTRAL      (fewer than 200 rows of history)
elif px > sma_50  and sma_50 > sma_200  -> UP
elif px < sma_50  and sma_50 < sma_200  -> DOWN
else                                    -> NEUTRAL
```
Strict inequalities: any tie is NEUTRAL. `sma_50` is not guarded (it averages however many rows exist), but it only matters once `sma_200` exists.

### 2. Anomaly flag and reason: [L75-79](../sql/03_signals.sql#L75)
```
sd_prev20  = stddev_samp(return_1d over the previous 20 rows, today excluded)      (L43)
vol_z      = (volume - avg(volume, previous 20 rows)) / stddev_samp(volume, previous 20 rows)    (L35-36, L58)
price_hit  = abs(return_1d) > 3 * sd_prev20          (NULL -> FALSE via COALESCE)
volume_hit = vol_z > 3                               (upper tail only; NULL or flat-volume window -> FALSE)
is_anomaly = price_hit OR volume_hit
anomaly_reason = 'price_and_volume_spike' | 'price_spike' | 'volume_spike' | NULL
```
Today is excluded from both windows so a spike cannot dilute its own threshold. `NULLIF(stddev, 0)` turns a zero-variance window into NULL, not an error.

### 3. 52-week breakout: [L97-126](../sql/03_signals.sql#L97)
A separate statement that always recomputes the **full** history of every ticker (Yahoo restates old `adj_close` after dividends).
```
window    = ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING                       (L102)  -> previous 252 sessions, today excluded
eligible  = n_prior = 252 AND adj_close IS NOT NULL AND high_raw > 0         (L106)
high_52w          = ROUND(max(adj_close in window), 2)                       if eligible else NULL   (L113)
breakout_52w      = adj_close > high_raw                                     if eligible else NULL   (L114)
pct_from_52w_high = ROUND((adj_close / high_raw - 1) * 100, 2)               if eligible else NULL   (L115)
```
The first 252 rows of each ticker are NULL (not FALSE). The flag compares the unrounded values; the stored `high_52w` and `pct_from_52w_high` are rounded, so in a rare tie they can look inconsistent.

### 4. Strong breakout: [L116](../sql/03_signals.sql#L116)
```
strong_breakout = adj_close > high_raw AND COALESCE(volume_zscore_20d > 2, FALSE)      if eligible else NULL
```
`volume_zscore_20d` is the same z-score as in signal 2 (previous 20 rows). A NULL z-score counts as FALSE, so the flag is FALSE (not NULL) on an eligible day.
The two volume thresholds differ on purpose: strong breakout needs z > 2, anomaly needs z > 3.

### 5. RSI zone: **no code classifies it**
What exists: `rsi_14`, [L44-45](../sql/03_signals.sql#L44) and [L55-57](../sql/03_signals.sql#L55):
```
gain = max(px - px[-1], 0); loss = max(-(px - px[-1]), 0)
avg_gain, avg_loss = SIMPLE 14-row averages (not Wilder smoothing)
rsi = 50 if avg_gain = 0 and avg_loss = 0; 100 if avg_loss = 0; else 100 - 100 / (1 + avg_gain / avg_loss)
```
`AVG` skips the NULL first-day difference, so a ticker's first rows average fewer than 14 values; there is no minimum-history guard. Because it is a simple average it differs slightly from charting packages.

What the dashboard draws: horizontal lines at **30 labelled "oversold" and 70 labelled "overbought"** ([`site/app.js` L268-269](../site/app.js#L268)); nothing is stored or counted.
The zone rule *rsi > 70 overbought, rsi < 30 oversold, otherwise neutral* is therefore a convention read off that chart. **The code does not say whether 30 and 70 are inclusive**; if the paper reports an RSI-zone signal it must state its own cut-offs and tie rule.

### 6. 20-day momentum sign: **sign is not stored**
What exists: `momentum_20d = px / NULLIF(px[-20], 0) - 1` ([L30](../sql/03_signals.sql#L30)), the price 20 **rows** earlier (about 4 weeks), stored rounded to 6 dp ([L67](../sql/03_signals.sql#L67)).
NULL for a ticker's first 20 rows. The dashboard colours a value "up" when `v >= 0` and "down" otherwise ([`site/app.js` L8](../site/app.js#L8)), i.e. **zero counts as positive** there,
while its `+` prefix is shown only for `v > 0` ([L7](../site/app.js#L7)). A sign rule for analysis should say how exact zero is handled; after 6-dp rounding a move smaller than 0.00005% stores as exactly 0.

### 7. Strongest sector: [`sql/04_views.sql` L30-50](../sql/04_views.sql#L30)
```
avg_momentum_20d(sector, day) = AVG(momentum_20d over the sector's stocks that have a non-NULL value that day)   (04_views L33; equal weight)
strength_rank = RANK() OVER (PARTITION BY day ORDER BY avg_momentum_20d DESC NULLS LAST)                          (04_views L49)
strongest sector = the sector with strength_rank = 1
relative_strength = avg_momentum_20d - mean over the 5 sectors of avg_momentum_20d                                (04_views L48)
```
The universe is 5 sectors x 5 stocks. `RANK` gives ties the same rank, so two tied sectors would both be rank 1; the dashboard lists sectors by `strength_rank`
([`export_site.py` L247-251](../src/export_site.py#L247)). The sector-rotation backtest instead takes the sector's **last non-NULL value in each month** and picks
`idxmax` ([`export_site.py` L161-162](../src/export_site.py#L161)), which breaks a tie by column order (alphabetical), and holds that sector for the next month.

## All stored columns (reference)

| Column | Formula | Threshold / NULL rule | Code |
|---|---|---|---|
| `return_1d` | `px / px[-1] - 1` | none | [L29](../sql/03_signals.sql#L29) |
| `momentum_20d`, `momentum_60d` | `px / px[-20] - 1`, `px / px[-60] - 1` | none | [L30-31](../sql/03_signals.sql#L30) |
| `volatility_20d` | `stddev_samp(return_1d, last 20 rows) * sqrt(252)` (fraction: 0.22 = 22%) | no minimum-history guard | [L42](../sql/03_signals.sql#L42), [L52](../sql/03_signals.sql#L52) |
| `sma_50`, `sma_200` | `avg(px)` over 50 / 200 rows | `sma_200` NULL unless 200 rows | [L32-34](../sql/03_signals.sql#L32), [L54](../sql/03_signals.sql#L54) |
| `rsi_14` | see signal 5 | 50 if flat, 100 if no losses | [L44-45](../sql/03_signals.sql#L44), [L55-57](../sql/03_signals.sql#L55) |
| `volume_zscore_20d` | see signal 2 | NULL if stddev is 0 | [L35-36](../sql/03_signals.sql#L35), [L58](../sql/03_signals.sql#L58) |
| `trend`, `is_anomaly`, `anomaly_reason` | signals 1 and 2 | | [L70-79](../sql/03_signals.sql#L70) |
| `high_52w`, `breakout_52w`, `pct_from_52w_high`, `strong_breakout` | signals 3 and 4 | NULL unless 252 prior rows | [L97-126](../sql/03_signals.sql#L97) |

`mv_sector_daily` / `v_sector_daily` ([`sql/04_views.sql`](../sql/04_views.sql#L30)) aggregate these per sector (`n_uptrend`, `n_downtrend`, `n_anomalies`, `breakout_count`, `strong_breakout_count` are counts of the flags above, not separate signals).
