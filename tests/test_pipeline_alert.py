import pytest

from scripts import pipeline_alert as A

CTX = A.RunContext(repo="o/r", run_id="99", run_url="https://github.com/o/r/actions/runs/99",
                   workflow="Daily market update", event="schedule", schedule="40 1 * * 2-6", sha="abcdef1")

LOG = "\n".join([
    "2026-11-04T01:40:01.0000000Z Run python src/daily_update.py",
    "2026-11-04T01:40:02.0000000Z connecting to postgresql://neondb_owner:s3cr3t@ep-x.neon.tech/neondb?sslmode=require",
    *[f"2026-11-04T01:40:03.0000000Z INFY.NS: inserted 0, updated 0, unchanged {i}" for i in range(80)],
    "2026-11-04T01:40:09.0000000Z \x1b[31mFAIL  freshness                  3  Stocks missing the latest trading day\x1b[0m",
    "2026-11-04T01:40:09.0000000Z Data-quality check failed",
    "2026-11-04T01:40:09.0000000Z ##[error]Process completed with exit code 1.",
])

JOBS = {"jobs": [
    {"id": 1, "name": "update", "conclusion": "failure", "html_url": "https://github.com/o/r/actions/runs/99/job/1",
     "steps": [{"name": "Set up job", "conclusion": "success"},
               {"name": "Refresh reporting layer + data-quality checks", "conclusion": "failure"},
               {"name": "Verify 52-week signals", "conclusion": "skipped"}]},
    {"id": 2, "name": "alert", "conclusion": None, "steps": []},
]}


class FakeGitHub:
    def __init__(self, issues=(), jobs=JOBS, log=LOG):
        self.issues = list(issues)
        self.jobs = jobs
        self.log = log
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/actions/runs/" in path:
            return 200, self.jobs
        if method == "GET" and path.startswith("/repos/o/r/issues?"):
            return 200, self.issues
        if method == "POST" and path == "/repos/o/r/issues":
            return 201, {"number": 42}
        if method == "POST" and path == "/repos/o/r/labels":
            return 422, {"message": "already_exists"}
        return 200, {}

    def job_log(self, job_id):
        return self.log

    def posted(self, method, suffix):
        return [b for m, p, b in self.calls if m == method and p.endswith(suffix)]


class TestRedaction:
    @pytest.mark.parametrize("raw", [
        "postgresql://user:pw@host.neon.tech/db?sslmode=require",
        "postgres://user:pw@localhost:5432/x",
        "password=hunter2",
        "GITHUB_TOKEN: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ])
    def test_secrets_never_survive(self, raw):
        out = A.redact(f"before {raw} after")
        assert "pw@" not in out and "hunter2" not in out and "ghp_abc" not in out
        assert out.startswith("before") and out.endswith("after")

    def test_excerpt_keeps_the_error_and_the_tail(self):
        ex = A.log_excerpt(LOG, tail=10)
        assert "FAIL  freshness" in ex and "exit code 1" in ex
        assert "s3cr3t" not in ex and "\x1b[" not in ex and "2026-11-04T" not in ex
        assert len(ex.splitlines()) < 40

    def test_excerpt_is_bounded(self):
        assert len(A.log_excerpt("x" * 100 + "\n" + "Error " * 5000, max_chars=500)) <= 500


class TestFailure:
    def test_opens_an_issue_when_none_is_open(self):
        gh = FakeGitHub()
        result = A.report_failure(gh, CTX)
        assert result == {"action": "opened", "issue": 42, "status": 201}
        body = gh.posted("POST", "/repos/o/r/issues")[0]
        assert body["title"] == A.TITLE and body["labels"] == [A.LABEL]
        assert "Refresh reporting layer + data-quality checks" in body["body"]
        assert "strict next-morning check" in body["body"] and CTX.run_url in body["body"]
        assert "FAIL  freshness" in body["body"] and "s3cr3t" not in body["body"]
        assert gh.posted("POST", "/labels")

    def test_comments_on_the_existing_issue_instead_of_duplicating(self):
        gh = FakeGitHub(issues=[{"number": 7, "title": A.TITLE}, {"number": 3, "title": A.TITLE}])
        result = A.report_failure(gh, CTX)
        assert result["action"] == "commented" and result["issue"] == 3
        assert gh.posted("POST", "/issues/3/comments")
        assert not gh.posted("POST", "/repos/o/r/issues")

    def test_pull_requests_with_the_label_are_ignored(self):
        gh = FakeGitHub(issues=[{"number": 5, "pull_request": {}}])
        assert A.report_failure(gh, CTX)["action"] == "opened"

    def test_unreadable_jobs_still_report(self):
        gh = FakeGitHub(jobs={"jobs": []})
        A.report_failure(gh, CTX)
        assert "could not be read" in gh.posted("POST", "/repos/o/r/issues")[0]["body"]

    def test_log_download_failure_does_not_stop_the_report(self):
        gh = FakeGitHub()
        gh.job_log = lambda job_id: (_ for _ in ()).throw(OSError("blob unavailable"))
        assert A.report_failure(gh, CTX)["action"] == "opened"


class TestResolve:
    def test_closes_the_open_issue(self):
        gh = FakeGitHub(issues=[{"number": 3}])
        assert A.resolve(gh, CTX) == {"action": "closed", "issue": 3}
        assert gh.posted("PATCH", "/issues/3")[0]["state"] == "closed"
        assert "Recovered" in gh.posted("POST", "/issues/3/comments")[0]["body"]

    def test_nothing_to_do_without_an_open_issue(self):
        gh = FakeGitHub()
        assert A.resolve(gh, CTX) == {"action": "none"}
        assert not [c for c in gh.calls if c[0] != "GET"]


def test_context_from_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "Bhavex/market-intelligence-pg")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef")
    monkeypatch.setenv("RUN_SCHEDULE", "40 11 * * 1-5")
    ctx = A.RunContext.from_env()
    assert ctx.run_url == "https://github.com/Bhavex/market-intelligence-pg/actions/runs/123"
    assert ctx.sha == "0123456"
    assert "first load" in A.build_report(ctx, [], {})
