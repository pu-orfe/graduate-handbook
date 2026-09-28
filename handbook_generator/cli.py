import os
import re
import sys
import argparse
from datetime import datetime
from handbook_generator.scraper import HandbookScraper
from handbook_generator.builder import HandbookBuilder
from handbook_generator.converter import DocumentConverter
from handbook_generator.snapshot import build_snapshot, snapshot_differs, write_snapshot

# Exit code for --skip-if-unchanged when the page matches the snapshot, so
# callers can tell "nothing to do" apart from both success and failure.
EXIT_UNCHANGED = 3


def get_copyright_year(soup):
    """Grabs the copyright year from the page footer, or None."""
    match = re.search(r"(?:©|Copyright|&copy;)\s*(\d{4})", soup.get_text())
    return match.group(1) if match else None


def main():
    parser = argparse.ArgumentParser(description="Princeton Graduate Handbook DOCX & PDF Scaffolding Generator")
    parser.add_argument("--url", default="https://orfe.princeton.edu/graduate/handbook", help="Handbook page URL to scrape")
    parser.add_argument("--template", default="template/graduate-handbook.docx", help="Path to the reference DOCX template (never overwritten)")
    parser.add_argument("--output-docx", default="output/graduate-handbook.docx", help="Path to generated output DOCX")
    parser.add_argument("--output-pdf", default="output/graduate-handbook.pdf", help="Path to generated output PDF")
    parser.add_argument("--media-dir", default="media", help="Directory to save extracted/scraped media")
    parser.add_argument("--snapshot", default="snapshot/handbook.md", help="Path to the normalized web content snapshot used for change detection")
    parser.add_argument("--skip-if-unchanged", action="store_true", help=f"Exit with code {EXIT_UNCHANGED} without building when the page matches the snapshot")
    parser.add_argument("--year", help="Year override for cover page (e.g. 2026)")
    parser.add_argument("--docx-only", action="store_true", help="Only generate DOCX, skip PDF conversion")
    parser.add_argument("--pdf-only", action="store_true", help="Only perform DOCX to PDF conversion of existing files")

    args = parser.parse_args()

    # The builder syncs the template in place; writing over it would make every
    # run diff against the previous run instead of the reference document.
    if os.path.abspath(args.template) == os.path.abspath(args.output_docx):
        print("Error: --output-docx must differ from --template.")
        sys.exit(1)

    os.makedirs(args.media_dir, exist_ok=True)
    for path in (args.output_docx, args.output_pdf):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    if args.pdf_only:
        if not os.path.exists(args.output_docx):
            print(f"Error: DOCX file not found at {args.output_docx}")
            sys.exit(1)
        converter = DocumentConverter(args.output_docx, args.output_pdf)
        try:
            converter.convert()
        except Exception as e:
            print(f"Error converting to PDF: {e}")
            sys.exit(1)
        sys.exit(0)

    # 1. Scraping
    print(f"Step 1: Fetching content from {args.url}...")
    scraper = HandbookScraper(args.url, args.media_dir)
    try:
        soup = scraper.fetch_page()
    except Exception as e:
        print(f"Error fetching webpage: {e}")
        if not os.environ.get("SCRAPER_HEADERS"):
            print("Hint: SCRAPER_HEADERS is not set; the site's bot protection needs it (see README).")
        sys.exit(1)

    print("Step 2: Parsing contact details and images...")
    scraper_data = scraper.parse_contacts_and_download_images(soup)
    print(f"Parsed Chair: {scraper_data['chair_name']}")
    print(f"Parsed DGS: {scraper_data['dgs_name']}")

    year = args.year
    if not year:
        year = get_copyright_year(soup)
        if year:
            print(f"Auto-detected academic year from webpage: {year}")
        else:
            year = str(datetime.now().year)
            print(f"Fallback to current system year: {year}")

    body_content = scraper.extract_main_content(soup)
    if not body_content:
        print("Error: Could not extract main body content from page HTML.")
        sys.exit(1)

    snapshot_text = build_snapshot(scraper_data, body_content, year)
    changed = snapshot_differs(args.snapshot, snapshot_text)
    print(f"Web content {'changed since' if changed else 'matches'} {args.snapshot}")
    outputs_present = os.path.exists(args.output_docx) and (args.docx_only or os.path.exists(args.output_pdf))
    if args.skip_if_unchanged and not changed and outputs_present:
        print("Web page matches the snapshot; skipping build.")
        sys.exit(EXIT_UNCHANGED)

    # 2. DOCX Building
    print(f"Step 3: Building DOCX document using template: {args.template}...")
    builder = HandbookBuilder(args.template, args.output_docx, args.media_dir)
    try:
        builder.build(scraper_data, body_content, year=year)
    except Exception as e:
        print(f"Error building DOCX: {e}")
        sys.exit(1)

    # 3. PDF Conversion
    if not args.docx_only:
        print("Step 4: Converting DOCX to PDF...")
        converter = DocumentConverter(args.output_docx, args.output_pdf)
        try:
            converter.convert()
        except Exception as e:
            print(f"Error converting to PDF: {e}")
            sys.exit(1)

    # Written last, so a failed build is retried on the next run rather than
    # recorded as already done.
    write_snapshot(args.snapshot, snapshot_text)
    print("Success! Process completed.")

if __name__ == "__main__":
    main()
