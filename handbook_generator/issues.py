"""Turns check findings into GitHub issues, one per problem, kept in sync.

    python -m handbook_generator.issues reports/findings.json [--dry-run]

Needs GITHUB_TOKEN (issues: write) and GITHUB_REPOSITORY (owner/name).

Each issue carries a hidden marker with its finding id, so on every run:
- a new finding opens an issue;
- a finding that is still present leaves its open issue alone (updating the
  text if the evidence changed);
- an open issue whose finding is gone is closed with a comment and the
  `auto-resolved` label - but only if its check actually ran this time, so a
  network failure in the link check never "resolves" a broken link;
- a finding that comes back reopens its issue if the bot closed it; if a
  person closed it, that decision stands and it is not reopened.
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

import requests

LABEL = "handbook-check"
AUTO_RESOLVED = "auto-resolved"
MARKER = re.compile(r"<!-- handbook-check:([0-9a-f]{16}) -->")
CHECK_MARKER = re.compile(r"<!-- handbook-check-name:([a-z_]+) -->")
PAGE = "https://orfe.princeton.edu/graduate/handbook"


def issue_body(finding):
    evidence = "\n".join(f"> {e}" for e in finding["evidence"]) or "> (none)"
    return (
        f"<!-- handbook-check:{finding['id']} -->\n"
        f"<!-- handbook-check-name:{finding['check']} -->\n"
        f"Found by the daily handbook check (`{finding['check']}`) on {PAGE}.\n\n"
        f"{finding['detail']}\n\n"
        f"**On the page:**\n\n{evidence}\n\n"
        "Fix it on the web page. The handbook is regenerated from the page, and this issue "
        "closes itself when the next check no longer finds the problem. If this is not "
        "actually a problem, close the issue and it will not be reopened.\n\n"
        "_Opened automatically by `.github/workflows/refresh-handbook.yml`._"
    )


@dataclass
class Plan:
    create: list = field(default_factory=list)       # findings
    update: list = field(default_factory=list)       # (issue, finding)
    reopen: list = field(default_factory=list)       # (issue, finding)
    close: list = field(default_factory=list)        # issues
    retire: list = field(default_factory=list)       # issues from checks no longer run
    respected: list = field(default_factory=list)    # (issue, finding): closed by a person
    kept: list = field(default_factory=list)         # issues left open, check did not run


def _labels(issue):
    return {l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])}


def plan(result, issues):
    """Decides what to do; pure, so it can be tested without GitHub."""
    by_id = {}
    for issue in issues:
        m = MARKER.search(issue.get("body") or "")
        if m and "pull_request" not in issue:
            # Prefer an open issue if a finding somehow has several.
            if m.group(1) not in by_id or issue["state"] == "open":
                by_id[m.group(1)] = issue
    findings = {f["id"]: f for f in result["findings"]}
    ran = set(result["checks_run"])
    available = result.get("checks_available")
    p = Plan()
    for fid, finding in findings.items():
        issue = by_id.get(fid)
        if issue is None:
            p.create.append(finding)
        elif issue["state"] == "open":
            if (issue.get("body") or "").strip() != issue_body(finding).strip():
                p.update.append((issue, finding))
        elif AUTO_RESOLVED in _labels(issue):
            p.reopen.append((issue, finding))
        else:
            p.respected.append((issue, finding))
    for fid, issue in by_id.items():
        if issue["state"] != "open" or fid in findings:
            continue
        m = CHECK_MARKER.search(issue.get("body") or "")
        if m and available is not None and m.group(1) not in available:
            p.retire.append(issue)
        elif m and m.group(1) in ran:
            p.close.append(issue)
        else:
            p.kept.append(issue)
    return p


class GitHub:
    def __init__(self, repo, token, session=None):
        self.base = f"https://api.github.com/repos/{repo}"
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}",
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def _call(self, method, path, **kw):
        r = self.s.request(method, self.base + path, timeout=30, **kw)
        if r.status_code >= 400 and not (method == "POST" and path == "/labels" and r.status_code == 422):
            raise RuntimeError(f"GitHub {method} {path}: {r.status_code} {r.text[:200]}")
        return r

    def ensure_labels(self):
        self._call("POST", "/labels", json={"name": LABEL, "color": "D93F0B",
                   "description": "Inconsistency found on the handbook web page"})
        self._call("POST", "/labels", json={"name": AUTO_RESOLVED, "color": "0E8A16",
                   "description": "Closed by the handbook check once the page was fixed"})

    def issues(self):
        out, page = [], 1
        while True:
            r = self._call("GET", "/issues", params={"labels": LABEL, "state": "all",
                                                    "per_page": 100, "page": page})
            batch = r.json()
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    def apply(self, p):
        for f in p.create:
            self._call("POST", "/issues", json={"title": f["title"], "body": issue_body(f), "labels": [LABEL]})
        for issue, f in p.update:
            self._call("PATCH", f"/issues/{issue['number']}", json={"title": f["title"], "body": issue_body(f)})
        for issue, f in p.reopen:
            self._call("PATCH", f"/issues/{issue['number']}", json={
                "state": "open", "title": f["title"], "body": issue_body(f),
                "labels": sorted((_labels(issue) - {AUTO_RESOLVED}) | {LABEL})})
            self._call("POST", f"/issues/{issue['number']}/comments",
                       json={"body": "The handbook check found this problem on the page again, so it has been reopened."})
        for issue in p.retire:
            self._call("POST", f"/issues/{issue['number']}/comments",
                       json={"body": "The check that raised this has been removed, so it will not be checked again. Closing."})
            self._call("PATCH", f"/issues/{issue['number']}", json={"state": "closed", "state_reason": "not_planned"})
        for issue in p.close:
            self._call("POST", f"/issues/{issue['number']}/comments",
                       json={"body": "The handbook check no longer finds this on the page, so it looks fixed. Closing."})
            self._call("PATCH", f"/issues/{issue['number']}", json={
                "state": "closed", "state_reason": "completed",
                "labels": sorted(_labels(issue) | {AUTO_RESOLVED})})


def summary(result, p):
    lines = [f"Checks run: {', '.join(result['checks_run']) or 'none'}"]
    for name, err in result["checks_failed"].items():
        lines.append(f"Check **{name}** could not run: {err} (its open issues were left alone)")
    lines.append(f"Findings: {len(result['findings'])}")
    lines += [f"- new issue: {f['title']}" for f in p.create]
    lines += [f"- updated #{i['number']}: {f['title']}" for i, f in p.update]
    lines += [f"- reopened #{i['number']}: {f['title']}" for i, f in p.reopen]
    lines += [f"- closed #{i['number']} (fixed): {i['title']}" for i in p.close]
    lines += [f"- closed #{i['number']} (check retired): {i['title']}" for i in p.retire]
    lines += [f"- #{i['number']} was closed by a person; not reopening: {f['title']}" for i, f in p.respected]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("findings")
    ap.add_argument("--dry-run", action="store_true", help="print the plan without touching GitHub")
    args = ap.parse_args(argv)
    with open(args.findings, encoding="utf-8") as f:
        result = json.load(f)

    if args.dry_run and not os.environ.get("GITHUB_TOKEN"):
        p = plan(result, [])
        print(summary(result, p))
        return 0
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        print("Error: GITHUB_REPOSITORY and GITHUB_TOKEN are required (or use --dry-run).")
        return 1
    gh = GitHub(repo, token)
    p = plan(result, gh.issues())
    text = summary(result, p)
    print(text)
    if not args.dry_run:
        gh.ensure_labels()
        gh.apply(p)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("### Handbook checks\n\n" + text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
