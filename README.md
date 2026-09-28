# ORFE Graduate Handbook

Generates the ORFE Ph.D. Handbook (DOCX and PDF) from the web version at
<https://orfe.princeton.edu/graduate/handbook>, and keeps it current with CI.

## How it stays up to date

| Workflow | Trigger | What it does |
|---|---|---|
| `refresh-handbook.yml` | Daily 11:23 UTC, or manual | Scrapes the page. If its content differs from `snapshot/handbook.md`, rebuilds `output/`, runs the tests, and opens (or updates) the `chore/refresh-handbook` pull request. Manual runs can tick **force** (rebuild even if the page is unchanged, e.g. after a template or code change) and **publish** (commit straight to `main` and release instead of opening a PR). |
| `release.yml` | Push to `main` that changes `output/` | Publishes the PDF and DOCX as a GitHub release. |
| `ci.yml` | Push / pull request | Runs the test suite in Docker. |

Latest files: `https://github.com/pu-orfe/graduate-handbook/releases/latest/download/graduate-handbook.pdf`
(and `.docx`).

**Change detection.** The DOCX and PDF embed build timestamps, so they differ
on every build and cannot show whether the page changed. `snapshot/handbook.md`
records only what feeds the document: the year, the Chair and DGS names, hashes
of their photos, and every body block, one per line. A web edit shows up as a
readable line diff in the refresh PR. Do not edit the snapshot by hand.

## Consistency checks → issues

Every refresh run also checks the page for problems a reader would trip over,
whether or not the page changed (`handbook_generator/checks.py`):

| Check | Flags |
|---|---|
| `roles` | A role naming different people in the profile cards and the Important Contacts list |
| `course_titles` | One course number with different titles in different places |
| `counted_lists` | "the six core courses" when the Core courses list has five |
| `dates` | A weekday that does not match its date; a year older than the handbook's previous year |
| `structure` | Section numbers that skip; a top-level web section missing from the template's contents |
| `links` | Links that return 404/410 or never answer, missing in-page anchors, malformed email links |

Each finding becomes a GitHub issue labelled `handbook-check`, with the evidence
quoted. The issue stays open while the problem is on the page, closes itself
(labelled `auto-resolved`) once a run no longer finds it, and reopens if it comes
back. An issue you close by hand stays closed. Removing a check closes its
open issues as "not planned". If a check cannot run (e.g. the
network fails during the link check), its issues are left untouched rather than
treated as fixed.

Run the checks locally without touching GitHub:

```zsh
./run.sh --docx-only --findings reports/findings.json --skip-if-unchanged
.venv/bin/python -m handbook_generator.issues --dry-run reports/findings.json
```

**Adding a model reviewer later:** write one more function in `checks.py` that
returns `Finding`s and add it to `CHECKS`; the reporter needs no changes. Keep it
optional (run only when an `ANTHROPIC_API_KEY` secret is set), have it quote the
conflicting passages verbatim, and drop any finding whose quotes are not on the page.

## Layout

- `template/graduate-handbook.docx` – the hand-formatted reference document. The
  builder matches web blocks to its paragraphs and updates them in place, so
  formatting carries over. The build never writes to it.
- `output/` – generated `graduate-handbook.docx` and `.pdf` (committed by CI).
- `snapshot/handbook.md` – normalized web content used for change detection.
- `handbook_generator/` – scraper, builder, converter, snapshot, CLI.

To change formatting or layout, edit the template and commit it. The next
manual refresh (or the next web change) rebuilds from it.

## Local use (macOS zsh)

```zsh
./run.sh                       # scrape + build natively (PDF via Word if installed)
./run.sh --docker              # build in the CI container (LibreOffice), writes output/ and snapshot/
./run.sh --skip-if-unchanged   # exit 3 without building if the page matches the snapshot
./test.sh                      # tests in Docker (includes a real LibreOffice conversion)
./test.sh --local              # tests in .venv
```

PDFs committed by CI are rendered by LibreOffice, using Carlito, Caladea and
Liberation fonts in place of Calibri, Cambria, Arial and Times New Roman.

**Pagination** is by rule, not position: the four major sections start on a new
page, headings stay with the text after them, and widow/orphan control is on.
The template's blank-line spacers (which only worked with Word's exact line
breaks) are collapsed in the body. The **contents page numbers** are then read
back from the rendered PDF and written into the contents list, and the PDF is
rendered again. The numbers in the DOCX therefore match the CI PDF; opened in
Word, the DOCX may paginate slightly differently. `tests/test_layout.py` checks
both in the container against a saved copy of the page (`tests/fixtures/handbook.html`).

## Scraper headers (not in this repository)

The site's bot protection rejects plain requests with 403. The extra header it
needs is kept out of this public repository and supplied through
`SCRAPER_HEADERS`, as `Name: value` lines or a JSON object:

- **CI:** the `SCRAPER_HEADERS` Actions secret (Settings → Secrets and variables → Actions).
- **Local:** a gitignored `.env` file next to `run.sh` containing
  `SCRAPER_HEADERS='Name: value'`. `run.sh` loads it for both native and `--docker` runs.

If the header stops working, the refresh workflow fails rather than skipping.
