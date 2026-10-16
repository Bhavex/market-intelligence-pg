-- =====================================================================
-- 07_trading_calendar.sql — NSE trading calendar + "which day should we have?"
-- Used by the data-quality check to prove no trading day was missed.
-- Safe to re-run. Add next year's holidays here each December.
-- =====================================================================

CREATE TABLE IF NOT EXISTS market_holidays (
    holiday_date  DATE PRIMARY KEY,
    occasion      TEXT NOT NULL
);

-- NSE equity segment trading holidays, 2026 (weekdays only)
INSERT INTO market_holidays (holiday_date, occasion) VALUES
  ('2026-01-15', 'Maharashtra municipal elections'),
  ('2026-01-26', 'Republic Day'),
  ('2026-03-03', 'Holi'),
  ('2026-03-26', 'Shri Ram Navami'),
  ('2026-03-31', 'Shri Mahavir Jayanti'),
  ('2026-04-03', 'Good Friday'),
  ('2026-04-14', 'Dr. Baba Saheb Ambedkar Jayanti'),
  ('2026-05-01', 'Maharashtra Day'),
  ('2026-05-28', 'Bakri Id'),
  ('2026-06-26', 'Muharram'),
  ('2026-09-14', 'Ganesh Chaturthi'),
  ('2026-10-02', 'Mahatma Gandhi Jayanti'),
  ('2026-10-20', 'Dussehra'),
  ('2026-11-10', 'Diwali - Balipratipada'),
  ('2026-11-24', 'Prakash Gurpurb Sri Guru Nanak Dev'),
  ('2026-12-25', 'Christmas')
ON CONFLICT (holiday_date) DO NOTHING;

-- Monday–Friday and not an exchange holiday
CREATE OR REPLACE FUNCTION is_trading_day(p_day DATE)
RETURNS BOOLEAN
LANGUAGE sql STABLE
AS $$
    SELECT EXTRACT(ISODOW FROM p_day) < 6
       AND NOT EXISTS (SELECT 1 FROM market_holidays WHERE holiday_date = p_day);
$$;

-- The most recent trading day whose closing prices should already be available.
-- Today counts only after 4:30 PM IST (market closes 3:30 PM; data settles after).
CREATE OR REPLACE FUNCTION expected_latest_trading_day(p_at TIMESTAMPTZ DEFAULT now())
RETURNS DATE
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    v_local TIMESTAMP := p_at AT TIME ZONE 'Asia/Kolkata';
    v_day   DATE      := v_local::date;
BEGIN
    IF v_local::time < TIME '16:30' OR NOT is_trading_day(v_day) THEN
        v_day := v_day - 1;
    END IF;
    WHILE NOT is_trading_day(v_day) LOOP
        v_day := v_day - 1;
    END LOOP;
    RETURN v_day;
END;
$$;

-- Freshness at a glance: which stocks are missing the expected day?
CREATE OR REPLACE VIEW v_data_freshness AS
SELECT c.ticker,
       MAX(p.price_date)              AS latest_price_date,
       expected_latest_trading_day()  AS expected_date,
       MAX(p.price_date) >= expected_latest_trading_day() AS is_fresh
FROM companies c
LEFT JOIN daily_prices p ON p.ticker = c.ticker
WHERE c.is_active AND c.in_dashboard        -- research stocks never fail the pipeline
GROUP BY c.ticker;

GRANT SELECT ON market_holidays, v_data_freshness TO readonly_access;
