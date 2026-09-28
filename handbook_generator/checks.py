"""Deterministic consistency checks on the handbook web page.

Each check compares facts the page states more than once, or states in a way
that can be verified, and returns findings. A finding's id is derived from the
facts involved, so the same problem keeps the same id from run to run: that
is what lets the issue reporter update, close and reopen issues instead of
filing duplicates.

A check that cannot run (e.g. the network is down for the link check) raises;
run_checks records it as not run, and the reporter then leaves that check's
open issues alone rather than closing them as resolved.

An optional model-based reviewer can be added later as one more check that
returns findings in the same shape; nothing here depends on it.
"""
import calendar
import datetime
import hashlib
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse

import requests

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
PHONE = re.compile(r"\b\d{3}-\d{3}-\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
COURSE = re.compile(r"\b([A-Z]{3})\s?(\d{3})([A-Z]?)\b")


@dataclass
class Finding:
    check: str
    title: str
    detail: str
    evidence: list = field(default_factory=list)
    key: str = ""

    @property
    def id(self):
        return hashlib.sha256(f"{self.check}\n{self.key or self.title}".encode()).hexdigest()[:16]

    def to_dict(self):
        return dict(asdict(self), id=self.id)


def _text(el):
    return " ".join(el.get_text(" ").split())


def _norm_name(name):
    return re.sub(r"[^a-z]", "", name.lower())


def _norm_title(title):
    return " ".join(re.findall(r"[a-z0-9]+", title.lower().replace("&", "and")))


# --- people and roles -------------------------------------------------------

def contact_entries(body):
    """Parses the Important Contacts list: [{role, name, phones, emails, text}]."""
    heading = next((h for h in body.find_all(re.compile(r"^h[1-6]$"))
                    if "important contacts" in _text(h).lower()), None)
    if heading is None:
        return []
    entries = []
    level = int(heading.name[1])
    for el in heading.find_all_next([re.compile(r"^h[1-6]$"), "li"]):
        if el.name.startswith("h") and int(el.name[1]) <= level:
            break  # end of the Important Contacts section
        if el.name != "li":
            continue
        text = _text(el)
        if not text or "," not in text:
            continue
        parts = [p.strip() for p in text.split(",")]
        role, rest = parts[0], parts[1:]
        name = ""
        for part in rest:
            candidate = re.sub(r"^Professor\s+", "", part).strip()
            if candidate and not PHONE.search(candidate) and not EMAIL.search(candidate) \
                    and not re.search(r"\d", candidate):
                name = candidate
                break
        entries.append({
            "role": role, "name": name, "text": text,
            "phones": PHONE.findall(text), "emails": EMAIL.findall(text),
        })
    return entries


def check_roles(soup, body, scraper, **_):
    """The same role must name the same person on the cards and in the contacts list."""
    findings = []
    cards = scraper._parse_people_cards(soup)
    contacts = contact_entries(body)
    for card in cards:
        for role in card["roles"]:
            for entry in contacts:
                if _norm_title(entry["role"]) != _norm_title(role) and not (
                    scraper.CHAIR_ROLE.search(role) and scraper.CHAIR_ROLE.search(entry["role"])
                    and "vice" not in (role + entry["role"]).lower()
                ):
                    continue
                if entry["name"] and _norm_name(entry["name"]) != _norm_name(card["name"]):
                    findings.append(Finding(
                        check="roles",
                        title=f"Page disagrees about the {role}: {card['name']} vs {entry['name']}",
                        detail=(f"The profile card lists **{card['name']}** as {role}, but the "
                                f"Important Contacts list says **{entry['name']}**. The handbook "
                                "uses the profile card for the welcome letter and prints the "
                                "contacts list as written."),
                        evidence=[f"Profile card: {card['name']} - {role}",
                                  f"Important Contacts: {entry['text']}"],
                        key=f"{_norm_title(role)}|{_norm_name(card['name'])}|{_norm_name(entry['name'])}",
                    ))
    return findings


# --- courses ------------------------------------------------------------------

def check_course_titles(body, **_):
    """One course number should not carry different titles in different places."""
    titles = {}
    for li in body.find_all("li"):
        text = _text(li)
        m = re.match(r"^((?:[A-Z]{3}\s?\d{3}[A-Z]?\s*/\s*)*[A-Z]{3}\s?\d{3}[A-Z]?)\s+(.+)$", text)
        if not m:
            continue
        title = re.sub(r"\s*\((?:Fall|Spring|Fall/Spring|Spring/Fall)\)\s*$", "", m.group(2)).strip()
        for code in COURSE.findall(m.group(1)):
            titles.setdefault("".join(code[:2]) + code[2], {}).setdefault(_norm_title(title), (title, text))
    findings = []
    for code, variants in titles.items():
        if len(variants) > 1:
            shown = [t for t, _ in variants.values()]
            findings.append(Finding(
                check="course_titles",
                title=f"{code[:3]} {code[3:]} has different titles on the page",
                detail="The same course number appears with different titles: " + "; ".join(f"“{t}”" for t in shown),
                evidence=[src for _, src in variants.values()],
                key=f"{code}|{'|'.join(sorted(variants))}",
            ))
    return findings


def check_counted_lists(body, **_):
    """ "the six core courses" must agree with the list under the "Core courses" heading."""
    findings = []
    text = _text(body)
    for heading in body.find_all(re.compile(r"^h[2-6]$")):
        noun = _text(heading).lower()
        nxt = heading.find_next_sibling()
        if nxt is None or nxt.name not in ("ul", "ol") or len(noun.split()) > 4:
            continue
        count = len(nxt.find_all("li", recursive=False))
        pattern = re.compile(
            rf"\b({'|'.join(NUMBER_WORDS)}|\d+)\s+(?:required\s+)?{re.escape(noun)}\b", re.I)
        for m in pattern.finditer(text):
            claimed = NUMBER_WORDS.get(m.group(1).lower()) or int(m.group(1))
            if claimed != count:
                start = max(0, m.start() - 60)
                findings.append(Finding(
                    check="counted_lists",
                    title=f"Page says {claimed} {noun} but lists {count}",
                    detail=(f"The text refers to **{m.group(0)}**, but the list under the "
                            f"“{_text(heading)}” heading has {count} items."),
                    evidence=["…" + text[start:m.end() + 60] + "…"],
                    key=f"{noun}|{claimed}|{count}",
                ))
    return findings


# --- dates --------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
WEEKDAYS = [d.lower() for d in calendar.day_name]
DATED = re.compile(
    r"\b(" + "|".join(WEEKDAYS) + r"),?\s+(" + "|".join(MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
YEAR = re.compile(r"\b(20\d{2})\b")


def check_dates(body, year=None, **_):
    """Weekdays must match their dates; years before the handbook's are likely stale."""
    findings = []
    text = _text(body)
    for m in DATED.finditer(text):
        weekday, month, day, yr = m.group(1).lower(), MONTHS[m.group(2).lower()], int(m.group(3)), int(m.group(4))
        try:
            actual = WEEKDAYS[datetime.date(yr, month, day).weekday()]
        except ValueError:
            actual = None
        if actual != weekday:
            findings.append(Finding(
                check="dates",
                title=f"Wrong weekday: “{m.group(0)}”",
                detail=(f"{m.group(2)} {day}, {yr} is a {actual.title() if actual else 'nonexistent date'}, "
                        f"not a {weekday.title()}."),
                evidence=[m.group(0)],
                key=m.group(0).lower(),
            ))
    if year:
        for m in YEAR.finditer(text):
            if int(m.group(1)) < int(year) - 1:
                start = max(0, m.start() - 60)
                findings.append(Finding(
                    check="dates",
                    title=f"Possibly stale year {m.group(1)} in the {year} handbook",
                    detail="A year more than one before the handbook's own; check it is still current.",
                    evidence=["…" + text[start:m.end() + 60] + "…"],
                    key=f"year|{m.group(1)}|{text[start:m.end() + 60]}",
                ))
    return findings


# --- structure ----------------------------------------------------------------

def check_structure(body, toc_titles=None, **_):
    """Section numbers run in order, and every top-level section is in the contents."""
    findings = []
    numbers = []
    for h in body.find_all(re.compile(r"^h[2-6]$")):
        m = re.match(r"^(\d+(?:\.\d+)*)\.?\s", _text(h))
        if m:
            numbers.append((tuple(int(x) for x in m.group(1).split(".")), _text(h)))
    for (prev, prev_text), (cur, cur_text) in zip(numbers, numbers[1:]):
        if len(cur) == len(prev) and cur[:-1] == prev[:-1] and cur[-1] != prev[-1] + 1:
            findings.append(Finding(
                check="structure",
                title=f"Section numbering jumps from {'.'.join(map(str, prev))} to {'.'.join(map(str, cur))}",
                detail="Consecutive headings are not numbered consecutively.",
                evidence=[prev_text, cur_text],
                key=f"{prev}|{cur}",
            ))
    if toc_titles:
        toc = [set(_norm_title(t).split()) for t in toc_titles]
        for h in body.find_all("h2"):
            words = set(_norm_title(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", _text(h))).split())
            if words and not any(len(words & t) >= max(1, len(words) * 0.6) for t in toc):
                findings.append(Finding(
                    check="structure",
                    title=f"Section “{_text(h)}” is not in the handbook's contents",
                    detail=("The web page has a top-level section the template's contents list "
                            "does not include; add it to template/graduate-handbook.docx."),
                    evidence=[_text(h)],
                    key=_norm_title(_text(h)),
                ))
    return findings


# --- links ----------------------------------------------------------------------

def check_links(soup, body, base_url, headers=None, session=None, **_):
    """Links in the handbook must resolve; in-page anchors must exist."""
    session = session or requests.Session()
    findings = []
    seen = {}
    host = urlparse(base_url).netloc
    for a in body.find_all("a", href=True):
        href = a["href"].strip()
        label = _text(a) or href
        if href.startswith("mailto:"):
            address = href[7:].split("?", 1)[0]
            if not EMAIL.fullmatch(address):
                findings.append(Finding("links", f"Malformed email link: {address}",
                                        f"The link “{label}” is not a valid address.", [href], key=href))
            continue
        if href.startswith("#"):
            target = href[1:]
            if target and not (soup.find(id=target) or soup.find(attrs={"name": target})):
                findings.append(Finding("links", f"Link to a missing section: {href}",
                                        f"“{label}” points to an anchor that is not on the page.",
                                        [href], key=href))
            continue
        url = urljoin(base_url, href)
        if urlparse(url).scheme not in ("http", "https") or url in seen:
            continue
        seen[url] = label
        status = _link_status(session, url, headers if urlparse(url).netloc == host else None)
        if status in (404, 410) or status == "unreachable":
            findings.append(Finding(
                "links", f"Broken link: {label}",
                f"<{url}> returned {status}.", [f"{label} → {url}"], key=url,
            ))
    return findings


def _link_status(session, url, headers):
    """HTTP status after two tries; "unreachable" if the host never answers.

    Only 404/410 and unreachable hosts count as broken: 401/403/429/5xx are
    usually bot protection or a bad moment, and would make noisy issues.
    """
    ua = {"User-Agent": "Mozilla/5.0 (handbook link check)"}
    status = "unreachable"
    for _ in range(2):
        try:
            r = session.get(url, headers={**ua, **(headers or {})}, timeout=20, allow_redirects=True, stream=True)
            r.close()
            status = r.status_code
            if status not in (404, 410):
                return status
        except requests.RequestException:
            status = "unreachable"
    return status


CHECKS = [check_roles, check_course_titles, check_counted_lists,
          check_dates, check_structure, check_links]


def run_checks(soup, body, scraper, year=None, toc_titles=None, skip=()):
    """Runs every check; returns {"checks_run": [...], "checks_failed": {...}, "findings": [...]}."""
    context = dict(soup=soup, body=body, scraper=scraper, year=year, toc_titles=toc_titles,
                   base_url=getattr(scraper, "url", None), headers=getattr(scraper, "headers", None))
    # checks_available lets the reporter close issues from checks since retired.
    result = {"checks_available": [c.__name__.removeprefix("check_") for c in CHECKS],
              "checks_run": [], "checks_failed": {}, "findings": []}
    for check in CHECKS:
        name = check.__name__.removeprefix("check_")
        if name in skip:
            continue
        try:
            findings = check(**context)
        except Exception as e:  # recorded, never silently treated as "no findings"
            result["checks_failed"][name] = f"{type(e).__name__}: {e}"
            continue
        result["checks_run"].append(name)
        result["findings"].extend(f.to_dict() for f in findings)
    return result
