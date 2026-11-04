import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

LABEL = "pipeline-failure"
TITLE = "Daily market update is failing"
API = "https://api.github.com"
MARKER = "<!-- pipeline-alert -->"

REDACTIONS = [
    (re.compile(r"postgres(?:ql)?://\S+", re.I), "postgresql://[redacted]"),
    (re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key)\s*[=:]\s*\S+"), r"\1=[redacted]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[redacted-token]"),
    (re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "[redacted-long-string]"),
]
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ")
SIGNAL = re.compile(r"(Traceback|Error|ERROR|FAIL|Exception|Too many failures|exit code|::error)")


def redact(text: str) -> str:
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def clean_log(text: str) -> list:
    return [STAMP.sub("", ANSI.sub("", line)).rstrip() for line in text.splitlines()]


def log_excerpt(text: str, tail: int = 40, max_chars: int = 6000) -> str:
    lines = [l for l in clean_log(text) if l.strip()]
    hits = [i for i, l in enumerate(lines) if SIGNAL.search(l)]
    keep = set(range(max(0, len(lines) - tail), len(lines)))
    for i in hits[-6:]:
        keep.update(range(max(0, i - 3), min(len(lines), i + 4)))
    out, last = [], -2
    for i in sorted(keep):
        if i != last + 1 and out:
            out.append("…")
        out.append(lines[i])
        last = i
    excerpt = redact("\n".join(out))
    return excerpt[-max_chars:]


@dataclass
class RunContext:
    repo: str
    run_id: str
    run_url: str
    workflow: str
    event: str
    schedule: str
    sha: str
    attempt: str = "1"

    @classmethod
    def from_env(cls):
        e = os.environ
        repo = e["GITHUB_REPOSITORY"]
        run_id = e["GITHUB_RUN_ID"]
        return cls(repo=repo, run_id=run_id,
                   run_url=f"{e.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}",
                   workflow=e.get("GITHUB_WORKFLOW", "Daily market update"), event=e.get("GITHUB_EVENT_NAME", ""),
                   schedule=e.get("RUN_SCHEDULE", ""), sha=e.get("GITHUB_SHA", "")[:7],
                   attempt=e.get("GITHUB_RUN_ATTEMPT", "1"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token
        self._opener = urllib.request.build_opener(NoRedirect)

    def request(self, method: str, path: str, body=None):
        req = urllib.request.Request(API + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28",
                                              "User-Agent": "market-intelligence-pipeline-alert"})
        try:
            with self._opener.open(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 307, 308):
                return exc.code, {"location": exc.headers.get("Location")}
            raw = exc.read()
            try:
                return exc.code, json.loads(raw) if raw else None
            except ValueError:
                return exc.code, None

    def download(self, url: str) -> str:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pipeline-alert"}), timeout=60) as r:
            return r.read().decode("utf-8", "replace")

    def job_log(self, job_id) -> str:
        status, data = self.request("GET", f"/repos/{self.repo}/actions/jobs/{job_id}/logs")
        if status in (301, 302, 307, 308) and data and data.get("location"):
            return self.download(data["location"])
        return ""


def failed_jobs(gh, ctx: RunContext) -> list:
    status, data = gh.request("GET", f"/repos/{ctx.repo}/actions/runs/{ctx.run_id}/jobs?per_page=50")
    if status != 200 or not data:
        return []
    out = []
    for job in data.get("jobs", []):
        if job.get("conclusion") not in ("failure", "timed_out", "cancelled"):
            continue
        steps = [s["name"] for s in job.get("steps", []) if s.get("conclusion") in ("failure", "timed_out", "cancelled")]
        out.append({"id": job["id"], "name": job["name"], "conclusion": job["conclusion"], "steps": steps,
                    "url": job.get("html_url", ctx.run_url)})
    return out


def build_report(ctx: RunContext, jobs: list, excerpts: dict) -> str:
    kind = {"40 1 * * 2-6": "strict next-morning check (07:10 IST)", "40 11 * * 1-5": "first load (17:10 IST)",
            "40 14 * * 1-5": "retry (20:10 IST)"}.get(ctx.schedule, ctx.event or "manual run")
    lines = [MARKER, f"**Run:** [{ctx.run_id}]({ctx.run_url}) · attempt {ctx.attempt} · {kind} · commit `{ctx.sha}`", ""]
    if not jobs:
        lines.append("The run failed, but the job details could not be read. Open the run for the full log.")
    for job in jobs:
        steps = ", ".join(f"`{s}`" for s in job["steps"]) or "(no step marked as failed)"
        lines += [f"**Job** [{job['name']}]({job['url']}): {job['conclusion']}; failed step: {steps}", ""]
        if excerpts.get(job["id"]):
            lines += ["<details><summary>Log excerpt (secrets redacted)</summary>", "", "```text",
                      excerpts[job["id"]], "```", "", "</details>", ""]
    lines += ["Re-running is safe: every load is an idempotent UPSERT. This issue is updated by each failed run and "
              "closed automatically by the next successful one."]
    return "\n".join(lines)


def open_issue(gh, repo: str):
    status, data = gh.request("GET", f"/repos/{repo}/issues?state=open&labels={LABEL}&per_page=10")
    if status != 200 or not data:
        return None
    issues = [i for i in data if "pull_request" not in i]
    return min(issues, key=lambda i: i["number"]) if issues else None


def ensure_label(gh, repo: str) -> None:
    gh.request("POST", f"/repos/{repo}/labels",
               {"name": LABEL, "color": "d03b3b", "description": "Opened automatically when the daily pipeline fails"})


def report_failure(gh, ctx: RunContext) -> dict:
    jobs = failed_jobs(gh, ctx)
    excerpts = {}
    for job in jobs:
        try:
            excerpts[job["id"]] = log_excerpt(gh.job_log(job["id"]))
        except Exception:
            excerpts[job["id"]] = ""
    body = build_report(ctx, jobs, excerpts)
    issue = open_issue(gh, ctx.repo)
    if issue:
        status, data = gh.request("POST", f"/repos/{ctx.repo}/issues/{issue['number']}/comments", {"body": body})
        return {"action": "commented", "issue": issue["number"], "status": status}
    ensure_label(gh, ctx.repo)
    status, data = gh.request("POST", f"/repos/{ctx.repo}/issues", {"title": TITLE, "body": body, "labels": [LABEL]})
    return {"action": "opened", "issue": (data or {}).get("number"), "status": status}


def resolve(gh, ctx: RunContext) -> dict:
    issue = open_issue(gh, ctx.repo)
    if not issue:
        return {"action": "none"}
    gh.request("POST", f"/repos/{ctx.repo}/issues/{issue['number']}/comments",
               {"body": f"{MARKER}\nRecovered: run [{ctx.run_id}]({ctx.run_url}) succeeded. Closing."})
    gh.request("PATCH", f"/repos/{ctx.repo}/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
    return {"action": "closed", "issue": issue["number"]}


def main():
    ap = argparse.ArgumentParser(description="Open, update or close the pipeline-failure issue for this workflow run")
    ap.add_argument("mode", choices=["failure", "success"])
    args = ap.parse_args()
    ctx = RunContext.from_env()
    gh = GitHub(ctx.repo, os.environ["GITHUB_TOKEN"])
    result = report_failure(gh, ctx) if args.mode == "failure" else resolve(gh, ctx)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
