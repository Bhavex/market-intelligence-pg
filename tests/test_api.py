import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_data
from marketintel.datasource import DataUnavailable, DbSource, DemoSource

TEST_DB = os.environ.get("TEST_DATABASE_URL")
DEMO = DemoSource()


class DownSource(DemoSource):
    def table(self, name):
        raise DataUnavailable("database unavailable: OperationalError")


@pytest.fixture
def client():
    app.dependency_overrides[get_data] = lambda: DEMO
    yield TestClient(app)
    app.dependency_overrides.clear()


def assert_error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["error"]["code"] == code and body["error"]["message"]
    return body["error"]


class TestMeta:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok" and r.json()["mode"] == "demo"

    def test_openapi_documents_every_endpoint(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for p in ("/v1/sectors", "/v1/companies", "/v1/companies/{ticker}", "/v1/companies/{ticker}/prices",
                  "/v1/companies/{ticker}/signals", "/v1/sectors/strength", "/v1/pipeline/status"):
            assert p in paths

    def test_get_responses_are_cacheable_and_cors_enabled(self, client):
        r = client.get("/v1/sectors", headers={"Origin": "https://example.com"})
        assert r.headers["cache-control"] == "public, max-age=300"
        assert r.headers["access-control-allow-origin"] == "*"

    def test_writes_are_not_allowed(self, client):
        assert_error(client.post("/v1/sectors"), 405, "method_not_allowed")
        assert_error(client.delete("/v1/companies/INFY.NS"), 405, "method_not_allowed")

    def test_unknown_route_uses_error_envelope(self, client):
        assert_error(client.get("/v1/nothing"), 404, "not_found")


class TestSectors:
    def test_lists_five_sectors_with_returns(self, client):
        data = client.get("/v1/sectors").json()
        assert [s["sector_name"] for s in data] == ["Auto", "Banking", "FMCG", "IT", "Pharma"]
        assert all(s["n_companies"] == 5 and s["return_1m_pct"] is not None for s in data)

    def test_sector_companies_is_case_insensitive(self, client):
        r = client.get("/v1/sectors/banking/companies")
        assert r.status_code == 200
        assert {c["ticker"] for c in r.json()} == {"HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS"}

    def test_unknown_sector(self, client):
        err = assert_error(client.get("/v1/sectors/Telecom/companies"), 404, "not_found")
        assert "Known sectors" in err["message"]

    def test_strength_defaults_to_latest_day_ranked(self, client):
        body = client.get("/v1/sectors/strength").json()
        rows = body["data"]
        assert len(rows) == 5 and len({r["price_date"] for r in rows}) == 1
        assert [r["strength_rank"] for r in rows] == [1, 2, 3, 4, 5]
        assert abs(sum(r["relative_strength"] for r in rows)) < 1e-5

    def test_strength_history_is_paginated(self, client):
        lo, hi = DEMO.date_range()
        body = client.get("/v1/sectors/strength", params={"start": "2026-01-01", "end": str(hi), "page_size": 7}).json()
        assert len(body["data"]) == 7 and body["pagination"]["has_next"]
        assert body["pagination"]["total"] % 5 == 0

    def test_strength_rejects_on_with_range(self, client):
        assert_error(client.get("/v1/sectors/strength", params={"on": "2026-10-01", "start": "2026-01-01"}),
                     400, "bad_request")


class TestCompanies:
    def test_list_paginates(self, client):
        body = client.get("/v1/companies", params={"page": 2, "page_size": 10}).json()
        assert len(body["data"]) == 10
        assert body["pagination"] == {"page": 2, "page_size": 10, "total": 25, "total_pages": 3, "has_next": True}
        last = client.get("/v1/companies", params={"page": 3, "page_size": 10}).json()
        assert len(last["data"]) == 5 and not last["pagination"]["has_next"]

    def test_filter_by_sector(self, client):
        body = client.get("/v1/companies", params={"sector": "IT"}).json()
        assert body["pagination"]["total"] == 5 and all(c["sector_name"] == "IT" for c in body["data"])

    def test_detail_includes_latest_quote(self, client):
        c = client.get("/v1/companies/infy.ns").json()
        assert c["ticker"] == "INFY.NS" and c["sector_name"] == "IT"
        assert c["close"] > 0 and c["latest_date"] == str(DEMO.date_range()[1])

    def test_ticker_with_ampersand(self, client):
        assert client.get("/v1/companies/M%26M.NS").json()["company_name"] == "Mahindra & Mahindra"

    @pytest.mark.parametrize("bad", ["INFY", "INFY.XX", "DROP TABLE.NS", "A" * 30 + ".NS"])
    def test_invalid_ticker_is_422(self, client, bad):
        err = assert_error(client.get(f"/v1/companies/{bad}"), 422, "invalid_request")
        assert err["details"][0]["field"] == "ticker"

    def test_unknown_ticker_is_404(self, client):
        assert_error(client.get("/v1/companies/NOPE.NS"), 404, "not_found")


class TestPrices:
    def test_date_filter_and_order(self, client):
        r = client.get("/v1/companies/TCS.NS/prices",
                       params={"start": "2026-03-01", "end": "2026-03-31", "order": "desc", "page_size": 500})
        rows = r.json()["data"]
        dates = [x["price_date"] for x in rows]
        assert dates == sorted(dates, reverse=True)
        assert dates[0] <= "2026-03-31" and dates[-1] >= "2026-03-01"
        assert r.json()["pagination"]["total"] == len(rows)
        assert set(rows[0]) == {"price_date", "open", "high", "low", "close", "adj_close", "volume"}
        assert all(x["low"] <= x["high"] for x in rows)

    def test_pages_do_not_overlap(self, client):
        a = client.get("/v1/companies/TCS.NS/prices", params={"page": 1, "page_size": 50}).json()["data"]
        b = client.get("/v1/companies/TCS.NS/prices", params={"page": 2, "page_size": 50}).json()["data"]
        assert a[-1]["price_date"] < b[0]["price_date"]

    def test_page_past_the_end_is_empty(self, client):
        body = client.get("/v1/companies/TCS.NS/prices", params={"page": 9999, "page_size": 500}).json()
        assert body["data"] == [] and not body["pagination"]["has_next"]

    def test_start_after_end_is_400(self, client):
        err = assert_error(client.get("/v1/companies/TCS.NS/prices", params={"start": "2026-05-01", "end": "2026-04-01"}),
                           400, "bad_request")
        assert "start" in err["message"]

    @pytest.mark.parametrize("params,field", [({"start": "2026-13-01"}, "start"), ({"page": 0}, "page"),
                                              ({"page_size": 501}, "page_size"), ({"order": "sideways"}, "order")])
    def test_invalid_query_parameters_are_422(self, client, params, field):
        err = assert_error(client.get("/v1/companies/TCS.NS/prices", params=params), 422, "invalid_request")
        assert err["details"][0]["field"] == field


class TestSignals:
    def test_signal_fields(self, client):
        row = client.get("/v1/companies/HDFCBANK.NS/signals", params={"order": "desc", "page_size": 1}).json()["data"][0]
        assert row["trend"] in ("UP", "DOWN", "NEUTRAL")
        assert 0 <= row["rsi_14"] <= 100
        assert "signal_date" in row and "price_date" not in row

    def test_anomalies_only(self, client):
        body = client.get("/v1/companies/SUNPHARMA.NS/signals", params={"anomalies_only": True, "page_size": 500}).json()
        assert body["data"] and all(r["is_anomaly"] for r in body["data"])

    def test_nan_becomes_null(self, client):
        lo, _ = DEMO.date_range()
        row = client.get("/v1/companies/TCS.NS/signals", params={"page_size": 1}).json()["data"][0]
        assert row["signal_date"] == str(lo)
        assert all(v is None or isinstance(v, (int, float, str, bool)) for v in row.values())


class TestPipeline:
    def test_status(self, client):
        s = client.get("/v1/pipeline/status").json()
        assert s["mode"] == "demo" and s["total_tickers"] == 25
        assert s["fresh_tickers"] + len(s["stale_tickers"]) == 25
        assert 0 <= s["daily_update_success_rate_30"] <= 1
        assert {j["job_name"] for j in s["jobs"]} >= {"daily_update"}


def test_database_outage_is_503():
    app.dependency_overrides[get_data] = lambda: DownSource()
    try:
        c = TestClient(app)
        assert_error(c.get("/v1/sectors"), 503, "data_unavailable")
        assert c.get("/health").json()["status"] == "degraded"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL with sample data loaded")
def test_live_database_mode():
    src = DbSource(TEST_DB)
    if src.query("SELECT COUNT(*) AS n FROM daily_prices")["n"].iloc[0] == 0:
        pytest.skip("test database has no prices")
    app.dependency_overrides[get_data] = lambda: src
    try:
        c = TestClient(app)
        assert c.get("/health").json()["mode"] == "database"
        assert len(c.get("/v1/sectors").json()) == 5
        body = c.get("/v1/companies/INFY.NS/prices", params={"page_size": 3, "order": "desc"}).json()
        assert len(body["data"]) == 3 and body["pagination"]["total"] > 3
        assert c.get("/v1/pipeline/status").status_code == 200
        assert len(c.get("/v1/sectors/strength").json()["data"]) == 5
    finally:
        app.dependency_overrides.clear()
