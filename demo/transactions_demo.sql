-- =====================================================================
-- transactions_demo.sql — ACID transactions, savepoints, triggers in action
-- Run each PART on its own in the Neon SQL Editor.
-- Every part ends in ROLLBACK, so the real data is never changed.
-- =====================================================================


-- ---------------------------------------------------------------------
-- PART 1 — Atomicity: either everything happens or nothing does
-- We correct a price AND recompute its signals together.
-- ROLLBACK at the end undoes both, as if nothing happened.
-- ---------------------------------------------------------------------
BEGIN;

UPDATE daily_prices
SET close = close * 1.10                    -- pretend Yahoo sent a corrected price
WHERE ticker = 'INFY.NS'
  AND price_date = (SELECT MAX(price_date) FROM daily_prices);

SELECT refresh_signals(CURRENT_DATE - 10);  -- signals updated inside the same transaction

-- the trigger has already written to the audit log (inside this transaction)
SELECT table_name, operation, row_key, old_close, new_close
FROM v_recent_changes LIMIT 3;

ROLLBACK;                                   -- undo: price, signals and audit row all vanish


-- ---------------------------------------------------------------------
-- PART 2 — Consistency: constraints stop bad data, the transaction aborts
-- ---------------------------------------------------------------------
BEGIN;

INSERT INTO daily_prices (ticker, price_date, close)
VALUES ('INFY.NS', CURRENT_DATE - 1000, -50);   -- violates CHECK (close > 0) -> ERROR

-- after an error PostgreSQL refuses further statements until ROLLBACK
ROLLBACK;


-- ---------------------------------------------------------------------
-- PART 3 — Savepoints: undo only part of a transaction
-- ---------------------------------------------------------------------
BEGIN;

UPDATE companies SET company_name = 'Infosys Limited' WHERE ticker = 'INFY.NS';

SAVEPOINT before_risky_step;

UPDATE companies SET is_active = FALSE WHERE ticker = 'TCS.NS';   -- oops, wrong company

ROLLBACK TO SAVEPOINT before_risky_step;     -- undo only the TCS change

SELECT ticker, company_name, is_active
FROM companies WHERE ticker IN ('INFY.NS', 'TCS.NS');   -- INFY renamed, TCS still active

ROLLBACK;                                    -- undo the rest too (demo only)


-- ---------------------------------------------------------------------
-- PART 4 — Stored procedure = one atomic business operation
-- replace_company() deactivates the old ticker and adds the new one.
-- ---------------------------------------------------------------------
BEGIN;

CALL replace_company('LUPIN.NS', 'ZYDUSLIFE.NS', 'Zydus Lifesciences');

SELECT ticker, company_name, is_active
FROM companies WHERE ticker IN ('LUPIN.NS', 'ZYDUSLIFE.NS');

ROLLBACK;                                    -- demo only: keep LUPIN in the real data

-- A failing call changes nothing at all:
--   CALL replace_company('DOESNOTEXIST.NS', 'X.NS', 'X');   -> ERROR, no partial changes


-- ---------------------------------------------------------------------
-- PART 5 — Validation trigger: future-dated prices are rejected
-- ---------------------------------------------------------------------
BEGIN;
INSERT INTO daily_prices (ticker, price_date, close)
VALUES ('INFY.NS', CURRENT_DATE + 30, 100);  -- ERROR from trg_validate_price()
ROLLBACK;


-- ---------------------------------------------------------------------
-- PART 6 — Isolation level (concept demo)
-- REPEATABLE READ: every query in this transaction sees the same snapshot,
-- even if the daily pipeline inserts new rows meanwhile.
-- ---------------------------------------------------------------------
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT COUNT(*) FROM daily_prices;           -- run again later in the same txn: same number
SHOW transaction_isolation;
COMMIT;
