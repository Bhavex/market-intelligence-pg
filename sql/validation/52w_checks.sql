-- =====================================================================
-- 52-week signal validation. Every check is ONE query returning the number of BAD rows
-- (0 = pass). Run by:  python src/verify_52w.py     (exit code 1 if any check fails)
-- Not in sql/*.sql on purpose: setup_db.py must not execute these.
-- Format: "-- CHECK <name>: <what it proves>" followed by one SELECT.
-- =====================================================================

-- CHECK all_or_nothing: the four columns are either all NULL or all set
SELECT COUNT(*) FROM signals
WHERE NOT ((high_52w IS NULL AND breakout_52w IS NULL AND pct_from_52w_high IS NULL AND strong_breakout IS NULL)
        OR (high_52w IS NOT NULL AND breakout_52w IS NOT NULL AND pct_from_52w_high IS NOT NULL AND strong_breakout IS NOT NULL));

-- CHECK first_252_rows_are_null: no value on a stock's first 252 trading rows
SELECT COUNT(*) FROM (
    SELECT s.high_52w, ROW_NUMBER() OVER (PARTITION BY p.ticker ORDER BY p.price_date) AS rn
    FROM daily_prices p JOIN signals s ON s.ticker = p.ticker AND s.signal_date = p.price_date
) t WHERE rn <= 252 AND high_52w IS NOT NULL;

-- CHECK no_extra_nulls: for tickers with no missing adj_close, every row after the 252nd has a value
SELECT COUNT(*) FROM (
    SELECT s.high_52w, ROW_NUMBER() OVER (PARTITION BY p.ticker ORDER BY p.price_date) AS rn,
           BOOL_OR(p.adj_close IS NULL) OVER (PARTITION BY p.ticker) AS has_gap
    FROM daily_prices p JOIN signals s ON s.ticker = p.ticker AND s.signal_date = p.price_date
) t WHERE rn > 252 AND NOT has_gap AND high_52w IS NULL;

-- CHECK breakout_matches_sign: breakout => pct >= 0 and not breakout => pct <= 0 (pct is rounded, so 0.00 is allowed on both)
SELECT COUNT(*) FROM signals
WHERE (breakout_52w AND pct_from_52w_high < 0) OR (NOT breakout_52w AND pct_from_52w_high > 0);

-- CHECK breakout_matches_price: breakout_52w = (adj_close > high), within the 2-decimal rounding of high_52w
SELECT COUNT(*) FROM signals s JOIN daily_prices p ON p.ticker = s.ticker AND p.price_date = s.signal_date
WHERE s.high_52w IS NOT NULL
  AND ((s.breakout_52w AND p.adj_close <= s.high_52w - 0.005)
    OR (NOT s.breakout_52w AND p.adj_close >= s.high_52w + 0.005));

-- CHECK strong_definition: strong_breakout = breakout AND volume_zscore_20d > 2 (NULL z-score counts as FALSE)
SELECT COUNT(*) FROM signals
WHERE high_52w IS NOT NULL
  AND strong_breakout IS DISTINCT FROM (breakout_52w AND COALESCE(volume_zscore_20d > 2, FALSE));

-- CHECK high_is_prior_max: high_52w equals the max adj_close of the previous 252 rows (re-derived with a self-join on row numbers)
WITH r AS (
    SELECT ticker, price_date, adj_close, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY price_date) AS rn
    FROM daily_prices
), calc AS (
    SELECT a.ticker, a.price_date, ROUND(MAX(b.adj_close), 2) AS hi, COUNT(b.adj_close) AS n
    FROM r a JOIN r b ON b.ticker = a.ticker AND b.rn BETWEEN a.rn - 252 AND a.rn - 1
    WHERE a.rn > 252 AND a.price_date >= (SELECT MAX(price_date) - 400 FROM daily_prices)   -- last ~400 days keeps the join cheap
    GROUP BY a.ticker, a.price_date
)
SELECT COUNT(*) FROM calc c JOIN signals s ON s.ticker = c.ticker AND s.signal_date = c.price_date
WHERE c.n = 252 AND s.high_52w IS DISTINCT FROM c.hi;

-- CHECK view_totals_match: v_sector_daily breakout counts equal the per-stock flags for every day and sector
SELECT COUNT(*) FROM (
    SELECT v.price_date, v.sector_name, v.breakout_count, v.strong_breakout_count,
           COUNT(*) FILTER (WHERE s.breakout_52w)    AS b,
           COUNT(*) FILTER (WHERE s.strong_breakout) AS sb
    FROM v_sector_daily v
    JOIN v_company_daily c ON c.price_date = v.price_date AND c.sector_name = v.sector_name
    LEFT JOIN signals s ON s.ticker = c.ticker AND s.signal_date = c.price_date
    GROUP BY v.price_date, v.sector_name, v.breakout_count, v.strong_breakout_count
) t WHERE breakout_count <> b OR strong_breakout_count <> sb;

-- CHECK view_old_columns_intact: v_sector_daily still starts with its original 10 columns, in order
SELECT CASE WHEN (SELECT string_agg(attname, ',' ORDER BY attnum) FROM pg_attribute
                  WHERE attrelid = 'v_sector_daily'::regclass AND attnum BETWEEN 1 AND 10 AND NOT attisdropped)
              = 'price_date,sector_name,n_stocks,avg_return_1d,avg_momentum_20d,avg_volatility_20d,avg_rsi_14,n_uptrend,n_downtrend,n_anomalies'
            THEN 0 ELSE 1 END;

-- CHECK reader_can_select: readonly_access (and powerbi_reader, if that login exists) can SELECT signals and the views
SELECT (SELECT COUNT(*) FROM (VALUES ('readonly_access'), ('powerbi_reader')) AS r(role)
        WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.role)
          AND NOT (has_table_privilege(r.role, 'signals', 'SELECT')
               AND has_table_privilege(r.role, 'v_sector_daily', 'SELECT')
               AND has_table_privilege(r.role, 'v_sector_strength', 'SELECT')
               AND has_column_privilege(r.role, 'signals', 'high_52w', 'SELECT')
               AND has_column_privilege(r.role, 'signals', 'strong_breakout', 'SELECT')));
