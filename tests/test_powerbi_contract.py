import os

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="set TEST_DATABASE_URL to a throwaway database with the schema applied")

PRICE_SIGNAL = ("open,high,low,close,adj_close,volume,return_1d,momentum_20d,momentum_60d,volatility_20d,sma_50,sma_200,"
                "rsi_14,volume_zscore_20d,trend,is_anomaly,anomaly_reason")
COMPANY_DAILY = "price_date,ticker,company_name,sector_name,city_name,state,region,latitude,longitude," + PRICE_SIGNAL
SECTOR_DAILY_10 = ("price_date,sector_name,n_stocks,avg_return_1d,avg_momentum_20d,avg_volatility_20d,avg_rsi_14,"
                   "n_uptrend,n_downtrend,n_anomalies")

CONTRACT = {
    "cities": "city_id,city_name,state,region,latitude,longitude",
    "sectors": "sector_id,sector_name,nifty_index,description",
    "companies": "ticker,company_name,sector_id,hq_city_id,is_active,added_at,in_dashboard,cap_group,index_membership,isin",
    "v_company_daily": COMPANY_DAILY,
    "v_latest_snapshot": COMPANY_DAILY,
    "v_sector_daily": SECTOR_DAILY_10 + ",breakout_count,strong_breakout_count",
    "mv_sector_daily": SECTOR_DAILY_10,
    "v_sector_strength": "price_date,sector_name,avg_momentum_20d,relative_strength,strength_rank",
    "v_region_daily": "price_date,region,state,n_stocks,avg_return_1d,avg_momentum_20d",
    "v_anomalies": "price_date,ticker,company_name,sector_name,close,return_1d,volume,volume_zscore_20d,anomaly_reason",
    "v_pipeline_health": "job_name,started_at,finished_at,status,rows_upserted,tickers_failed,message,latest_price_date",
    "v_data_freshness": "ticker,latest_price_date,expected_date,is_fresh",
}


@pytest.fixture(scope="module")
def conn():
    import psycopg
    c = psycopg.connect(TEST_DB)
    yield c
    c.close()


def columns(conn, name):
    return [r[0] for r in conn.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
        (f"public.{name}",)).fetchall()]


@pytest.mark.parametrize("name", sorted(CONTRACT))
def test_existing_columns_are_kept_in_order(conn, name):
    expected = CONTRACT[name].split(",")
    actual = columns(conn, name)
    assert actual[:len(expected)] == expected, f"{name}: Power BI columns were removed, renamed or reordered"


@pytest.mark.parametrize("name", sorted(CONTRACT))
def test_readonly_role_can_still_read(conn, name):
    ok = conn.execute("SELECT has_table_privilege('readonly_access', %s, 'SELECT')", (f"public.{name}",)).fetchone()[0]
    assert ok, f"readonly_access lost SELECT on {name}"
