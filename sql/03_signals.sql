-- =====================================================================
-- 03_signals.sql — signal engine written in SQL (window functions)
--   SELECT refresh_signals('2024-01-01');   -- recompute from a date
--   SELECT refresh_signals();               -- default: last 10 days
-- Returns the number of signal rows upserted by the main statement.
-- The 52-week columns are a second statement that ALWAYS covers the full history of every
-- ticker (Yahoo restates old adj_close after dividends, so recomputing only new dates would
-- leave stale highs behind). It runs after volume_zscore_20d is written (strong_breakout).
-- =====================================================================

CREATE OR REPLACE FUNCTION refresh_signals(p_from DATE DEFAULT CURRENT_DATE - 10)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows INTEGER;
    v_52w  INTEGER;
BEGIN
    WITH base AS (
        -- 400 calendar days of look-back so the 200-day SMA is correct at p_from
        SELECT ticker, price_date, volume,
               COALESCE(adj_close, close) AS px
        FROM daily_prices
        WHERE price_date >= p_from - 400
    ),
    r AS (
        SELECT b.*,
               px - LAG(px) OVER w                               AS diff_1d,
               px / NULLIF(LAG(px) OVER w, 0) - 1                AS ret_1d,
               px / NULLIF(LAG(px, 20) OVER w, 0) - 1            AS mom_20,
               px / NULLIF(LAG(px, 60) OVER w, 0) - 1            AS mom_60,
               AVG(px) OVER (w ROWS BETWEEN 49  PRECEDING AND CURRENT ROW) AS sma_50,
               AVG(px) OVER (w ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma_200,
               COUNT(*) OVER (w ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS n_200,
               AVG(volume)         OVER (w ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS vol_avg_prev20,
               STDDEV_SAMP(volume) OVER (w ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS vol_sd_prev20
        FROM base b
        WINDOW w AS (PARTITION BY ticker ORDER BY price_date)
    ),
    r2 AS (
        SELECT r.*,
               STDDEV_SAMP(ret_1d) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sd_20,
               STDDEV_SAMP(ret_1d) OVER (w ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS sd_prev20,
               AVG(GREATEST(diff_1d, 0)) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain,
               AVG(GREATEST(-diff_1d, 0)) OVER (w ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss
        FROM r
        WINDOW w AS (PARTITION BY ticker ORDER BY price_date)
    ),
    final AS (
        SELECT ticker, price_date AS signal_date,
               ret_1d, mom_20, mom_60,
               sd_20 * SQRT(252)                                   AS vol_20,
               sma_50,
               CASE WHEN n_200 = 200 THEN sma_200 END              AS sma_200,
               CASE WHEN avg_loss = 0 AND avg_gain = 0 THEN 50
                    WHEN avg_loss = 0 THEN 100
                    ELSE 100 - 100 / (1 + avg_gain / avg_loss) END AS rsi,
               (volume - vol_avg_prev20) / NULLIF(vol_sd_prev20, 0) AS vol_z,
               sd_prev20,
               px
        FROM r2
    )
    INSERT INTO signals AS s (ticker, signal_date, return_1d, momentum_20d, momentum_60d,
                              volatility_20d, sma_50, sma_200, rsi_14, volume_zscore_20d,
                              trend, is_anomaly, anomaly_reason, computed_at)
    SELECT ticker, signal_date,
           ROUND(ret_1d::numeric, 6), ROUND(mom_20::numeric, 6), ROUND(mom_60::numeric, 6),
           ROUND(vol_20::numeric, 6), ROUND(sma_50::numeric, 4), ROUND(sma_200::numeric, 4),
           ROUND(rsi::numeric, 2), ROUND(vol_z::numeric, 4),
           CASE WHEN sma_200 IS NULL               THEN 'NEUTRAL'
                WHEN px > sma_50 AND sma_50 > sma_200 THEN 'UP'
                WHEN px < sma_50 AND sma_50 < sma_200 THEN 'DOWN'
                ELSE 'NEUTRAL' END,
           -- Anomaly = price move > 3 sigma of the prior 20 days, or volume z-score > 3
           COALESCE(ABS(ret_1d) > 3 * sd_prev20, FALSE) OR COALESCE(vol_z > 3, FALSE),
           CASE WHEN COALESCE(ABS(ret_1d) > 3 * sd_prev20, FALSE) AND COALESCE(vol_z > 3, FALSE)
                     THEN 'price_and_volume_spike'
                WHEN COALESCE(ABS(ret_1d) > 3 * sd_prev20, FALSE) THEN 'price_spike'
                WHEN COALESCE(vol_z > 3, FALSE)               THEN 'volume_spike' END,
           now()
    FROM final
    WHERE signal_date >= p_from
    ON CONFLICT (ticker, signal_date) DO UPDATE SET
           return_1d = EXCLUDED.return_1d, momentum_20d = EXCLUDED.momentum_20d,
           momentum_60d = EXCLUDED.momentum_60d, volatility_20d = EXCLUDED.volatility_20d,
           sma_50 = EXCLUDED.sma_50, sma_200 = EXCLUDED.sma_200, rsi_14 = EXCLUDED.rsi_14,
           volume_zscore_20d = EXCLUDED.volume_zscore_20d, trend = EXCLUDED.trend,
           is_anomaly = EXCLUDED.is_anomaly, anomaly_reason = EXCLUDED.anomaly_reason,
           computed_at = EXCLUDED.computed_at;

    GET DIAGNOSTICS v_rows = ROW_COUNT;

    -- 52-week high / breakout, over the FULL daily_prices history (no date filter).
    -- Window = the previous 252 trading rows of adj_close, today excluded. A row gets values only
    -- if all 252 prior adj_close values exist; otherwise all four columns stay NULL.
    -- Only rows whose values actually changed are written (no daily rewrite of ~70k rows).
    WITH hi AS (
        SELECT ticker, price_date, adj_close,
               MAX(adj_close)   OVER w52 AS high_raw,
               COUNT(adj_close) OVER w52 AS n_prior
        FROM daily_prices
        WINDOW w52 AS (PARTITION BY ticker ORDER BY price_date ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING)
    ),
    f AS (
        SELECT hi.ticker, hi.price_date,
               (hi.n_prior = 252 AND hi.adj_close IS NOT NULL AND hi.high_raw > 0) AS eligible,
               hi.adj_close, hi.high_raw, z.volume_zscore_20d AS vol_z
        FROM hi
        JOIN signals z ON z.ticker = hi.ticker AND z.signal_date = hi.price_date
    ),
    calc AS (
        SELECT ticker, price_date,
               CASE WHEN eligible THEN ROUND(high_raw::numeric, 2) END                              AS high_52w,
               CASE WHEN eligible THEN adj_close > high_raw END                                     AS breakout_52w,
               CASE WHEN eligible THEN ROUND(((adj_close / high_raw - 1) * 100)::numeric, 2) END    AS pct_from_52w_high,
               CASE WHEN eligible THEN adj_close > high_raw AND COALESCE(vol_z > 2, FALSE) END      AS strong_breakout
        FROM f
    )
    UPDATE signals s
    SET high_52w = c.high_52w, breakout_52w = c.breakout_52w,
        pct_from_52w_high = c.pct_from_52w_high, strong_breakout = c.strong_breakout,
        computed_at = now()
    FROM calc c
    WHERE s.ticker = c.ticker AND s.signal_date = c.price_date
      AND (s.high_52w, s.breakout_52w, s.pct_from_52w_high, s.strong_breakout)
          IS DISTINCT FROM (c.high_52w, c.breakout_52w, c.pct_from_52w_high, c.strong_breakout);

    GET DIAGNOSTICS v_52w = ROW_COUNT;
    RAISE NOTICE 'refresh_signals: % signal rows upserted, % rows with new 52-week values', v_rows, v_52w;
    RETURN v_rows;
END;
$$;
