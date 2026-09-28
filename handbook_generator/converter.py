import os
import sys
import subprocess
import shutil

class DocumentConverter:
    def __init__(self, docx_path, pdf_path):
        self.docx_path = docx_path
        self.pdf_path = pdf_path

    def convert(self):
        """Converts the DOCX file to PDF using macOS Word or LibreOffice headless."""
        docx_abs = os.path.abspath(self.docx_path)
        pdf_abs = os.path.abspath(self.pdf_path)

        if not os.path.exists(docx_abs):
            raise FileNotFoundError(f"DOCX file not found at {docx_abs}")

        print(f"Converting {docx_abs} to {pdf_abs}...")

        # 1. Try docx2pdf (macOS / Windows with Word installed)
        if sys.platform == "darwin":
            word_app = "/Applications/Microsoft Word.app"
            if os.path.exists(word_app):
                try:
                    from docx2pdf import convert
                    convert(docx_abs, pdf_abs)
                    print(f"PDF successfully generated at {pdf_abs} using MS Word.")
                    return True
                except Exception as e:
                    print(f"Warning: docx2pdf conversion failed: {e}. Trying fallback...")

        # 2. Try LibreOffice/soffice (Linux, Docker, or fallback on Mac)
        soffice_bin = self._find_soffice()
        if soffice_bin:
            # LibreOffice can exit 0 without writing anything; clear the target
            # first so a PDF from a previous run cannot pass for this one.
            if os.path.exists(pdf_abs):
                os.remove(pdf_abs)
            try:
                out_dir = os.path.dirname(pdf_abs)
                cmd = [
                    soffice_bin,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    out_dir,
                    docx_abs
                ]
                print(f"Running LibreOffice command: {' '.join(cmd)}")
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # LibreOffice converts to the same filename but with .pdf
                generated_pdf = os.path.splitext(docx_abs)[0] + ".pdf"
                if os.path.exists(generated_pdf) and generated_pdf != pdf_abs:
                    shutil.move(generated_pdf, pdf_abs)
                if not os.path.exists(pdf_abs):
                    raise RuntimeError(f"LibreOffice reported success but wrote no PDF at {pdf_abs}")

                print(f"PDF successfully generated at {pdf_abs} using LibreOffice.")
                return True
            except Exception as e:
                print(f"Error: LibreOffice conversion failed: {e}")

        # 3. Fail if no converter available
        raise RuntimeError("No PDF conversion engine found (Microsoft Word or LibreOffice is required).")

    def _find_soffice(self):
        """Locates the soffice/libreoffice binary on the system path or common locations."""
        # 1. Search system PATH
        path_bin = shutil.which("soffice") or shutil.which("libreoffice")
        if path_bin:
            return path_bin

        # 2. Search common macOS install paths
        mac_paths = [
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/libreoffice"
        ]
        for p in mac_paths:
            if os.path.exists(p):
                return p

        # 3. Search common Linux install paths
        linux_paths = [
            "/usr/bin/soffice",
            "/usr/bin/libreoffice",
            "/usr/local/bin/soffice"
        ]
        for p in linux_paths:
            if os.path.exists(p):
                return p

        return None


def render_pdf(docx_path, pdf_path, max_passes=3):
    """Converts to PDF, then corrects the contents page numbers until stable.

    Numbers only change on the contents page, whose length stays the same, so
    the second pass normally confirms the first; a document that keeps
    changing is an error rather than a PDF with wrong numbers.
    """
    from handbook_generator.toc import update_toc_numbers

    converter = DocumentConverter(docx_path, pdf_path)
    converter.convert()
    for _ in range(max_passes):
        if not update_toc_numbers(docx_path, pdf_path):
            return True
        print("Contents page numbers corrected; rendering again...")
        converter.convert()
    raise RuntimeError(f"Contents page numbers did not settle after {max_passes} passes")
