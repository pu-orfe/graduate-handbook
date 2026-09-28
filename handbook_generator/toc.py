"""Keeps the contents page's page numbers true to the rendered PDF.

The template's table of contents is a hand-curated list of plain-text entries
("1.1<tab>Research & Thesis Adviser<tab>5"): no PAGEREF fields, so no renderer
can update it, and regenerating it from heading styles produces a worse list
(the synced body's heading styles are inconsistent). Instead: render once,
find the page each entry's heading landed on, write those numbers into the
entries, and render again. The contents page's length does not change, so the
second render paginates identically.
"""
import re

import docx
from docx.oxml.ns import qn
from pypdf import PdfReader

STOP_WORDS = {"a", "an", "and", "the", "of", "for", "to", "in", "s"}
# Headings are short; anything longer on a line is body prose.
MAX_HEADING_CHARS = 90
MIN_SCORE = 0.6


def _words(text):
    words = re.findall(r"[a-z0-9]+", text.lower().replace("’", "'"))
    return [w.rstrip("s") or w for w in words if w not in STOP_WORDS and not w.isdigit()]


def _score(entry_words, line):
    line_words = set(_words(line))
    if not entry_words:
        return 0.0
    return sum(w in line_words for w in entry_words) / len(entry_words)


def toc_entries(document):
    """Returns [(title, [<w:t> elements holding the page number])] for the contents list.

    Word often splits a number like "11" across several runs, so the number is
    every trailing all-digit text node, not just the last one.
    """
    entries = []
    for sdt in document.element.body.iter(qn("w:sdt")):
        for p in sdt.iter(qn("w:p")):
            texts = [t for t in p.iter(qn("w:t")) if t.text]
            if not texts or not list(p.iter(qn("w:tab"))):
                continue
            number_ts = []
            for t in reversed(texts):
                if re.fullmatch(r"\s*\d*\s*", t.text) and (t.text.strip() or number_ts):
                    number_ts.insert(0, t)
                else:
                    break
            if not "".join(t.text for t in number_ts).strip():
                continue
            title_text = "".join(t.text for t in texts[: len(texts) - len(number_ts)])
            title = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", title_text).strip()
            entries.append((title, number_ts))
        if entries:
            break
    return entries


def _set_number(number_ts, value):
    first = next(t for t in number_ts if t.text.strip())
    first.text = re.sub(r"\d+", str(value), first.text.strip(), count=1)
    for t in number_ts:
        if t is not first:
            t.text = ""


def pdf_page_lines(pdf_path):
    return [
        [line.strip() for line in (page.extract_text() or "").splitlines() if line.strip()]
        for page in PdfReader(pdf_path).pages
    ]


def locate(titles, pages, first_body_page=2):
    """Page (1-based) of each title's heading, searched in order after the previous one.

    A line containing every word of the entry wins, wherever it is; only if
    there is none does the closest partial match count, and then only on a
    heading-length line. Prose often shares most of a heading's words ("admitted
    to the final public oral (FPO) examination" vs "Final Public Oral (FPO):
    Department Instructions"), so partial matches never jump ahead of an exact one.
    """
    found = []
    cursor = (first_body_page - 1, 0)

    def lines_after(pos):
        for pi in range(pos[0], len(pages)):
            for li in range(pos[1] if pi == pos[0] else 0, len(pages[pi])):
                yield pi, li, pages[pi][li]

    for title in titles:
        wanted = _words(title)
        hit = None
        partial = None
        for pi, li, line in lines_after(cursor):
            if len(line) > MAX_HEADING_CHARS:
                continue
            score = _score(wanted, line)
            if score == 1.0:
                hit = (pi, li)
                break
            if (score >= MIN_SCORE and len(_words(line)) <= len(wanted) + 3
                    and (partial is None or score > partial[0])):
                partial = (score, pi, li)
        if hit is None and partial is not None:
            hit = partial[1:]
        if hit is None:
            raise ValueError(f"Contents entry {title!r} matches no heading in the rendered PDF")
        cursor = (hit[0], hit[1] + 1)
        found.append(hit[0] + 1)
    return found


def update_toc_numbers(docx_path, pdf_path):
    """Rewrites the contents numbers from the PDF; returns True if any changed."""
    document = docx.Document(docx_path)
    entries = toc_entries(document)
    if not entries:
        print("Warning: no contents list found; page numbers not checked.")
        return False
    pages = locate([title for title, _ in entries], pdf_page_lines(pdf_path))
    changed = False
    for (title, number_ts), page in zip(entries, pages):
        old = "".join(t.text for t in number_ts).strip()
        if old != str(page):
            print(f"Contents: '{title}' {old} -> {page}")
            _set_number(number_ts, page)
            changed = True
    if changed:
        document.save(docx_path)
    return changed
