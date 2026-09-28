import json

import pytest

from handbook_generator import issues
from handbook_generator.issues import AUTO_RESOLVED, LABEL, GitHub, issue_body, plan


def finding(fid, check="contacts", title=None):
    return {"id": fid, "check": check, "title": title or f"Problem {fid}",
            "detail": "detail", "evidence": ["evidence"], "key": fid}


def issue(number, f, state="open", labels=(LABEL,), body=None):
    return {"number": number, "title": f["title"], "state": state,
            "labels": [{"name": l} for l in labels], "body": body if body is not None else issue_body(f)}


def result(findings, ran=("contacts", "links")):
    return {"checks_run": list(ran), "checks_failed": {}, "findings": findings}


A, B, C = (finding("a" * 16), finding("b" * 16), finding("c" * 16, check="links"))


def test_new_finding_creates_issue():
    p = plan(result([A]), [])
    assert p.create == [A] and not (p.update or p.reopen or p.close)


def test_existing_open_issue_is_left_alone():
    p = plan(result([A]), [issue(1, A)])
    assert not (p.create or p.update or p.reopen or p.close)


def test_changed_evidence_updates_issue():
    p = plan(result([A]), [issue(1, A, body=issue_body(A).replace("evidence", "old evidence"))])
    assert [i["number"] for i, _ in p.update] == [1]


def test_gone_finding_closes_issue():
    p = plan(result([]), [issue(1, A)])
    assert [i["number"] for i in p.close] == [1]


def test_gone_finding_from_a_check_that_did_not_run_is_kept():
    # The link check failed (network), so its issue must not be "resolved".
    p = plan(result([], ran=("contacts",)), [issue(3, C)])
    assert p.close == [] and [i["number"] for i in p.kept] == [3]


def test_returning_finding_reopens_auto_resolved_issue():
    p = plan(result([A]), [issue(1, A, state="closed", labels=(LABEL, AUTO_RESOLVED))])
    assert [i["number"] for i, _ in p.reopen] == [1]


def test_issue_closed_by_a_person_is_not_reopened():
    p = plan(result([A]), [issue(1, A, state="closed")])
    assert p.reopen == [] and p.create == [] and [i["number"] for i, _ in p.respected] == [1]


def test_pull_requests_and_unmarked_issues_are_ignored():
    pr = dict(issue(5, A), pull_request={})
    unmarked = {"number": 6, "title": "x", "state": "open", "labels": [], "body": "hand-written"}
    p = plan(result([A]), [pr, unmarked])
    assert p.create == [A] and p.close == []


class FakeSession:
    def __init__(self, issues_payload):
        self.headers, self.calls, self.issues_payload = {}, [], issues_payload

    def request(self, method, url, **kw):
        self.calls.append((method, url.split("/repos/o/r")[1], kw.get("json"), kw.get("params")))

        class R:
            status_code = 422 if (method == "POST" and url.endswith("/labels")) else 200
            text = ""

            def json(inner):
                return self.issues_payload

        return R()


def test_apply_makes_the_expected_calls():
    s = FakeSession([])
    gh = GitHub("o/r", "token", session=s)
    p = plan(result([A]), [issue(2, B), issue(3, finding("d" * 16), state="closed", labels=(LABEL, AUTO_RESOLVED))])
    # d is closed and gone; b is open and gone -> close; a is new -> create.
    gh.ensure_labels()  # 422 "already exists" is fine
    gh.apply(p)
    calls = [(m, path) for m, path, _, _ in s.calls]
    assert ("POST", "/issues") in calls
    assert ("POST", "/issues/2/comments") in calls and ("PATCH", "/issues/2") in calls
    close = next(j for m, path, j, _ in s.calls if m == "PATCH" and path == "/issues/2")
    assert close["state"] == "closed" and AUTO_RESOLVED in close["labels"]
    create = next(j for m, path, j, _ in s.calls if m == "POST" and path == "/issues")
    assert create["labels"] == [LABEL] and f"<!-- handbook-check:{A['id']} -->" in create["body"]
    assert s.headers["Authorization"] == "Bearer token"


def test_api_errors_raise():
    class Failing(FakeSession):
        def request(self, method, url, **kw):
            class R:
                status_code, text = 403, "Resource not accessible by integration"
            return R()

    with pytest.raises(RuntimeError, match="403"):
        GitHub("o/r", "t", session=Failing([])).apply(plan(result([A]), []))


def test_main_requires_credentials(tmp_path, monkeypatch, capsys):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(result([A])))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert issues.main([str(path)]) == 1
    assert issues.main([str(path), "--dry-run"]) == 0
    assert "new issue: Problem" in capsys.readouterr().out
