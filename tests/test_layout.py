"""End-to-end layout checks on the real template and a saved copy of the page.

Runs where CI renders the PDF (the container: LibreOffice and poppler). REQUIRE_SOFFICE=1 there turns a missing tool into a failure instead
of a skip.

tests/fixtures/handbook.html is the public page as of September 2026. The
checks are about layout, not wording, so the fixture only needs refreshing if
the page's structure changes.
"""
import os
import re
import shutil
import subprocess

import pytest
from bs4 import BeautifulSoup

from handbook_generator.builder import HandbookBuilder
from handbook_generator.converter import DocumentConverter, render_pdf
from handbook_generator.scraper import decode_cloudflare_emails

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "handbook.html")
TEMPLATE = os.path.join(ROOT, "template", "graduate-handbook.docx")
MEDIA = os.path.join(ROOT, "media")

TOOLS_READY = (
    DocumentConverter("", "")._find_soffice() is not None
    and shutil.which("pdftotext") is not None
)
pytestmark = pytest.mark.skipif(
    not TOOLS_READY and not os.environ.get("REQUIRE_SOFFICE"),
    reason="needs LibreOffice and pdftotext; runs in the container",
)

SECTIONS = {
    "1": "Ph.D. Program Requirements",
    "2": "Other Regulations",
    "3": "Miscellaneous Information",
    "4": "Important Contacts",
}
# A page with fewer lines than this is the "one or two sentences spilled
# over" failure - unless it is the natural end of a major section.
MIN_LINES = 8


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("layout")
    soup = BeautifulSoup(open(FIXTURE, encoding="utf-8").read(), "html.parser")
    decode_cloudflare_emails(soup)
    body = soup.find_all("div", class_="field--name-field-ps-body")
    body = max(body, key=lambda d: len(d.text))
    data = {
        "chair_name": "Amir Ali Ahmadi",
        "dgs_name": "Ludovic Tangpi",
        "chair_img_path": os.path.join(MEDIA, "image2.jpeg"),
        "dgs_img_path": os.path.join(MEDIA, "image3.jpeg"),
    }
    docx_path = str(tmp / "handbook.docx")
    pdf_path = str(tmp / "handbook.pdf")
    HandbookBuilder(TEMPLATE, docx_path, MEDIA).build(data, body, year="2026")
    render_pdf(docx_path, pdf_path)
    return pdf_path


@pytest.fixture(scope="module")
def pages(pdf):
    text = subprocess.run(["pdftotext", "-layout", pdf, "-"], check=True,
                          capture_output=True, text=True).stdout
    result = [[line for line in page.splitlines() if line.strip()] for page in text.split("\f")]
    while result and not result[-1]:
        result.pop()
    return result


def _section_start_pages(pages):
    """Page number (1-based) on which each major section heading appears in the body."""
    found = {}
    for number, title in SECTIONS.items():
        pattern = re.compile(rf"^\s*(?:{number}\.?\s+)?{re.escape(title)}\s*$")
        for idx, page in enumerate(pages[1:], start=2):  # page 1 holds the contents list
            if any(pattern.match(line) for line in page):
                found[number] = idx
                break
    return found


def test_every_major_section_found(pages):
    assert set(_section_start_pages(pages)) == set(SECTIONS)


def test_no_spillover_pages(pages):
    starts = set(_section_start_pages(pages).values())
    short = []
    for idx, page in enumerate(pages, start=1):
        if idx <= 2:  # cover/contents and the welcome letter are laid out by the template
            continue
        ends_section = idx == len(pages) or (idx + 1) in starts
        if len(page) < MIN_LINES and not ends_section:
            short.append((idx, len(page), page[0].strip()[:60] if page else ""))
    assert not short, f"pages with only a few lines that do not end a section: {short}"


def test_major_sections_start_at_top_of_page(pages):
    for number, page_no in _section_start_pages(pages).items():
        first = pages[page_no - 1][0]
        assert SECTIONS[number] in first, f"section {number} is not first on page {page_no}: {first!r}"


def test_contents_page_numbers_match(pages):
    contents = pages[0]
    starts = _section_start_pages(pages)
    for number, title in SECTIONS.items():
        entry = next((l for l in contents if re.search(rf"{re.escape(title)}\s+\d+\s*$", l)), None)
        assert entry, f"no contents entry for {title!r}"
        listed = int(re.search(r"(\d+)\s*$", entry).group(1))
        assert listed == starts[number], f"contents lists {title!r} on page {listed}, it starts on {starts[number]}"


def test_body_pages_have_a_top_margin(pdf):
    """The template's zero top margin once put text against the top edge."""
    bbox = subprocess.run(["pdftotext", "-bbox", pdf, "-"], check=True,
                          capture_output=True, text=True).stdout
    tops, page = {}, 0
    for line in bbox.splitlines():
        if "<page " in line:
            page += 1
        elif "<word " in line and page not in tops:
            tops[page] = float(re.search(r'yMin="([\d.]+)"', line).group(1)) / 72
    tight = {p: round(t, 2) for p, t in tops.items() if p > 1 and t < 0.9}
    assert tops and not tight, f"pages whose text starts under 0.9in from the top: {tight}"


def test_page_numbers_continue_after_cover(pages):
    # The cover is its own section; LibreOffice would restart numbering at 1.
    for number in (2, 3, len(pages)):
        assert pages[number - 1][-1].strip() == str(number)
