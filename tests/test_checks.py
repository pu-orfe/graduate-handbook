import os

import pytest
import requests
from bs4 import BeautifulSoup

from handbook_generator import checks
from handbook_generator.checks import run_checks
from handbook_generator.scraper import HandbookScraper, decode_cloudflare_emails

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "handbook.html")


def card(name, role):
    return f"""<div class="content-list-item">
      <img alt="{name}" src="/{name.split()[-1].lower()}.jpg"/>
      <span class="field--name-title"><a href="#">{name}</a></span>
      <div class="field--name-field-ps-people-position">{role}</div></div>"""


def page(body_html, cards=""):
    soup = BeautifulSoup(
        f"<html><body>{cards}<div class='field--name-field-ps-body'>{body_html}</div></body></html>",
        "html.parser")
    return soup, soup.find("div", class_="field--name-field-ps-body")


CONTACTS = """<h2>Important Contacts</h2><ul>
  <li>Chair, Professor {chair}{chair_phone}</li>
  <li>Director of Graduate Studies, Professor Ludovic Tangpi, 609-258-4558</li>
  <li>Department Manager, Connie Brown, {manager_phone}</li>
  <li>Graduate Program Administrator, Kimberly Lupinacci, 609-258-4018</li>
</ul><p>Welcome to the department, we wish you success!</p>"""


def contacts(chair="Amir Ali Ahmadi", chair_phone=", 609-258-5130", manager_phone="609-258-5422"):
    return CONTACTS.format(chair=chair, chair_phone=chair_phone, manager_phone=manager_phone)


@pytest.fixture
def scraper(tmp_path):
    return HandbookScraper(url="https://orfe.example.edu/graduate/handbook", media_dir=str(tmp_path))


def titles(findings):
    return [f.title for f in findings]


# --- roles ---

def test_roles_disagreement_is_found(scraper):
    soup, body = page(contacts(chair="Mete Soner"), cards=card("Amir Ali Ahmadi", "Department Chair"))
    found = checks.check_roles(soup=soup, body=body, scraper=scraper)
    assert titles(found) == ["Page disagrees about the Department Chair: Amir Ali Ahmadi vs Mete Soner"]
    assert "Chair, Professor Mete Soner, 609-258-5130" in found[0].evidence[1]


def test_roles_agreement_is_quiet(scraper):
    soup, body = page(contacts(), cards=card("Amir Ali Ahmadi", "Department Chair"))
    assert checks.check_roles(soup=soup, body=body, scraper=scraper) == []


def test_vice_chair_is_not_the_chair(scraper):
    soup, body = page(contacts(), cards=card("Someone Else", "Vice Chair"))
    assert checks.check_roles(soup=soup, body=body, scraper=scraper) == []


# --- contacts ---

def test_contact_missing_phone_is_found():
    _, body = page(contacts(chair_phone=""))
    assert titles(checks.check_contacts(body=body)) == ["Contact without a phone number: Chair"]


def test_closing_sentence_is_not_a_contact():
    _, body = page(contacts())
    assert [e["role"] for e in checks.contact_entries(body)] == [
        "Chair", "Director of Graduate Studies", "Department Manager", "Graduate Program Administrator"]


def test_shared_phone_is_found():
    _, body = page(contacts(manager_phone="609-258-4018"))
    assert titles(checks.check_contacts(body=body)) == ["Same phone number listed for different people: 609-258-4018"]


# --- courses and counts ---

def test_course_with_two_titles_is_found():
    _, body = page("<ul><li>ORF 522 Linear &amp; Nonlinear Optimization (Fall)</li></ul>"
                   "<ul><li>ORF 522 Convex Optimization</li><li>MAT 572/APC 572 Topics</li></ul>")
    assert titles(checks.check_course_titles(body=body)) == ["ORF 522 has different titles on the page"]


def test_course_repeated_with_same_title_is_quiet():
    _, body = page("<ul><li>ORF 522 Linear Optimization (Fall)</li></ul><ul><li>ORF 522 Linear Optimization</li></ul>")
    assert checks.check_course_titles(body=body) == []


def core(n):
    return "<h3>Core courses</h3><ul>" + "".join(f"<li>ORF 52{i} X</li>" for i in range(n)) + "</ul>"


def test_count_mismatch_is_found():
    _, body = page("<p>Students take the six core courses.</p>" + core(5))
    assert titles(checks.check_counted_lists(body=body)) == ["Page says 6 core courses but lists 5"]


def test_count_match_and_required_wording_is_quiet():
    _, body = page("<p>Students take the six core courses and pass all six required core courses.</p>" + core(6))
    assert checks.check_counted_lists(body=body) == []


# --- dates ---

def test_wrong_weekday_is_found():
    _, body = page("<p>Orientation is on Monday, September 8, 2026 and Tuesday, September 8, 2026.</p>")
    assert titles(checks.check_dates(body=body, year="2026")) == ["Wrong weekday: “Monday, September 8, 2026”"]


def test_stale_year_is_found():
    _, body = page("<p>Apply by May 1, 2023. Revised 2025.</p>")
    assert titles(checks.check_dates(body=body, year="2026")) == ["Possibly stale year 2023 in the 2026 handbook"]


# --- structure ---

def test_numbering_gap_is_found():
    _, body = page("<h3>2.3 Changes</h3><h3>2.5 Auditing</h3><h3>2.6 TAs</h3>")
    assert titles(checks.check_structure(body=body)) == ["Section numbering jumps from 2.3 to 2.5"]


def test_new_top_level_section_missing_from_contents():
    _, body = page("<h2>Ph.D. Program Requirements</h2><h2>Wellness Resources</h2>")
    found = checks.check_structure(body=body, toc_titles=["Ph.D. Program Requirements", "Other Regulations"])
    assert titles(found) == ["Section “Wellness Resources” is not in the handbook's contents"]


# --- links ---

class FakeSession:
    def __init__(self, statuses):
        self.statuses, self.calls = statuses, []

    def get(self, url, **kw):
        self.calls.append((url, kw.get("headers", {})))
        status = self.statuses[url]
        if isinstance(status, Exception):
            raise status
        r = requests.Response()
        r.status_code = status
        r._content, r._content_consumed = b"", True
        return r


def test_links(scraper):
    soup, body = page(
        '<a href="/graduate/forms">Forms</a><a href="https://gone.example.org/x">Gone</a>'
        '<a href="https://blocked.example.org/">Blocked</a><a href="https://down.example.org/">Down</a>'
        '<a href="#here">ok anchor</a><a href="#nowhere">bad anchor</a>'
        '<a href="mailto:klupinac@princeton.edu?subject=x">mail</a><a href="mailto:not-an-address">bad mail</a>'
        '<span id="here"></span>')
    session = FakeSession({
        "https://orfe.example.edu/graduate/forms": 200,
        "https://gone.example.org/x": 404,
        "https://blocked.example.org/": 403,  # bot protection is not "broken"
        "https://down.example.org/": requests.ConnectionError("no route"),
    })
    found = checks.check_links(soup=soup, body=body, base_url=scraper.url,
                               headers={"X-Bypass": "1"}, session=session)
    assert sorted(titles(found)) == sorted([
        "Broken link: Gone", "Broken link: Down",
        "Link to a missing section: #nowhere", "Malformed email link: not-an-address",
    ])
    # The site's own header goes only to the site, never to third parties.
    sent = dict(session.calls)
    assert sent["https://orfe.example.edu/graduate/forms"].get("X-Bypass") == "1"
    assert "X-Bypass" not in sent["https://gone.example.org/x"]


# --- running ---

def test_failed_check_is_recorded_not_silent(scraper, monkeypatch):
    def boom(**_):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(checks, "CHECKS", [checks.check_dates, boom])
    soup, body = page("<p>text</p>")
    result = run_checks(soup, body, scraper, year="2026")
    assert result["checks_run"] == ["dates"]
    assert "network down" in result["checks_failed"]["boom"]


def test_finding_ids_are_stable_and_distinct():
    a = checks.Finding("contacts", "t", "d", key="k1")
    assert a.id == checks.Finding("contacts", "t", "other detail", key="k1").id
    assert a.id != checks.Finding("contacts", "t", "d", key="k2").id


def test_real_page_findings(scraper):
    """Guards against false positives on the actual page (links skipped: network).

    The saved page (September 2026) has one genuine problem: the Chair's
    contact entry lost its phone number when the name was corrected.
    """
    soup = BeautifulSoup(open(FIXTURE, encoding="utf-8").read(), "html.parser")
    decode_cloudflare_emails(soup)
    body = scraper.extract_main_content(soup)
    result = run_checks(soup, body, scraper, year="2026",
                        toc_titles=["Ph.D. Program Requirements", "Other Regulations",
                                    "Miscellaneous Information", "Important Contacts"],
                        skip={"links"})
    assert result["checks_failed"] == {}
    assert [f["title"] for f in result["findings"]] == ["Contact without a phone number: Chair"]
