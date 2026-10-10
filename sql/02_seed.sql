-- =====================================================================
-- 02_seed.sql — sectors, HQ cities and the 25-stock universe
-- Safe to re-run (ON CONFLICT DO NOTHING / DO UPDATE).
-- =====================================================================

INSERT INTO sectors (sector_name, nifty_index, description) VALUES
  ('IT',      'NIFTY IT',      'Information technology services'),
  ('Banking', 'NIFTY BANK',    'Private and public sector banks'),
  ('Auto',    'NIFTY AUTO',    'Automobile manufacturers'),
  ('Pharma',  'NIFTY PHARMA',  'Pharmaceuticals'),
  ('FMCG',    'NIFTY FMCG',    'Fast-moving consumer goods')
ON CONFLICT (sector_name) DO NOTHING;

-- Approximate city-centre coordinates (for Power BI map visuals)
INSERT INTO cities (city_name, state, region, latitude, longitude) VALUES
  ('Mumbai',    'Maharashtra',   'West',  19.076000, 72.877700),
  ('Pune',      'Maharashtra',   'West',  18.520400, 73.856700),
  ('Bengaluru', 'Karnataka',     'South', 12.971600, 77.594600),
  ('Hyderabad', 'Telangana',     'South', 17.385000, 78.486700),
  ('New Delhi', 'Delhi',         'North', 28.613900, 77.209000),
  ('Noida',     'Uttar Pradesh', 'North', 28.535500, 77.391000),
  ('Gurugram',  'Haryana',       'North', 28.459500, 77.026600),
  ('Ghaziabad', 'Uttar Pradesh', 'North', 28.669200, 77.453800),
  ('Kolkata',   'West Bengal',   'East',  22.572600, 88.363900)
ON CONFLICT (city_name, state) DO NOTHING;

-- Company universe: 5 sectors x 5 large caps (NSE, Yahoo '.NS' symbols)
WITH src (ticker, company_name, sector_name, city_name) AS (VALUES
  -- IT
  ('TCS.NS',        'Tata Consultancy Services',       'IT',      'Mumbai'),
  ('INFY.NS',       'Infosys',                         'IT',      'Bengaluru'),
  ('WIPRO.NS',      'Wipro',                           'IT',      'Bengaluru'),
  ('HCLTECH.NS',    'HCL Technologies',                'IT',      'Noida'),
  ('TECHM.NS',      'Tech Mahindra',                   'IT',      'Pune'),
  -- Banking
  ('HDFCBANK.NS',   'HDFC Bank',                       'Banking', 'Mumbai'),
  ('ICICIBANK.NS',  'ICICI Bank',                      'Banking', 'Mumbai'),
  ('SBIN.NS',       'State Bank of India',             'Banking', 'Mumbai'),
  ('KOTAKBANK.NS',  'Kotak Mahindra Bank',             'Banking', 'Mumbai'),
  ('AXISBANK.NS',   'Axis Bank',                       'Banking', 'Mumbai'),
  -- Auto (TATAMOTORS.NS dropped after the demerger broke the symbol)
  ('MARUTI.NS',     'Maruti Suzuki India',             'Auto',    'New Delhi'),
  ('M&M.NS',        'Mahindra & Mahindra',             'Auto',    'Mumbai'),
  ('BAJAJ-AUTO.NS', 'Bajaj Auto',                      'Auto',    'Pune'),
  ('EICHERMOT.NS',  'Eicher Motors',                   'Auto',    'New Delhi'),
  ('HEROMOTOCO.NS', 'Hero MotoCorp',                   'Auto',    'New Delhi'),
  -- Pharma
  ('SUNPHARMA.NS',  'Sun Pharmaceutical Industries',   'Pharma',  'Mumbai'),
  ('DRREDDY.NS',    'Dr. Reddy''s Laboratories',       'Pharma',  'Hyderabad'),
  ('CIPLA.NS',      'Cipla',                           'Pharma',  'Mumbai'),
  ('DIVISLAB.NS',   'Divi''s Laboratories',            'Pharma',  'Hyderabad'),
  ('LUPIN.NS',      'Lupin',                           'Pharma',  'Mumbai'),
  -- FMCG
  ('HINDUNILVR.NS', 'Hindustan Unilever',              'FMCG',    'Mumbai'),
  ('ITC.NS',        'ITC',                             'FMCG',    'Kolkata'),
  ('NESTLEIND.NS',  'Nestle India',                    'FMCG',    'Gurugram'),
  ('BRITANNIA.NS',  'Britannia Industries',            'FMCG',    'Kolkata'),
  ('DABUR.NS',      'Dabur India',                     'FMCG',    'Ghaziabad')
)
INSERT INTO companies (ticker, company_name, sector_id, hq_city_id)
SELECT s.ticker, s.company_name, sec.sector_id, c.city_id
FROM src s
JOIN sectors sec ON sec.sector_name = s.sector_name
JOIN cities  c   ON c.city_name     = s.city_name
ON CONFLICT (ticker) DO UPDATE
   SET company_name = EXCLUDED.company_name,
       sector_id    = EXCLUDED.sector_id,
       hq_city_id   = EXCLUDED.hq_city_id;
