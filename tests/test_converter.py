import os
import sys
import docx
import pytest
from handbook_generator import converter as converter_module
from handbook_generator.converter import DocumentConverter


def _docx(tmp_path):
    path = tmp_path / "in.docx"
    d = docx.Document()
    d.add_paragraph("Converter smoke test")
    d.save(str(path))
    return path


def test_missing_docx_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        DocumentConverter(str(tmp_path / "nope.docx"), str(tmp_path / "out.pdf")).convert()


def test_no_engine_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(DocumentConverter, "_find_soffice", lambda self: None)
    with pytest.raises(RuntimeError, match="No PDF conversion engine"):
        DocumentConverter(str(_docx(tmp_path)), str(tmp_path / "out.pdf")).convert()


def test_libreoffice_output_is_renamed(tmp_path, monkeypatch):
    src = _docx(tmp_path)
    target = tmp_path / "renamed.pdf"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # LibreOffice names its output after the input file.
        (tmp_path / "in.pdf").write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(DocumentConverter, "_find_soffice", lambda self: "/usr/bin/soffice")
    monkeypatch.setattr(converter_module.subprocess, "run", fake_run)

    assert DocumentConverter(str(src), str(target)).convert() is True
    assert calls and calls[0][:4] == ["/usr/bin/soffice", "--headless", "--convert-to", "pdf"]
    assert target.read_bytes().startswith(b"%PDF")
    assert not (tmp_path / "in.pdf").exists()


# The container sets REQUIRE_SOFFICE=1 so a missing LibreOffice fails the
# suite there instead of skipping - CI builds the real PDF with it.
@pytest.mark.skipif(
    not os.environ.get("REQUIRE_SOFFICE") and DocumentConverter("", "")._find_soffice() is None,
    reason="LibreOffice not installed (set REQUIRE_SOFFICE=1 to make this a failure)",
)
def test_real_libreoffice_conversion(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # force the LibreOffice path even on macOS
    target = tmp_path / "out.pdf"
    DocumentConverter(str(_docx(tmp_path)), str(target)).convert()
    assert target.read_bytes().startswith(b"%PDF")


def test_libreoffice_silent_failure_is_an_error(tmp_path, monkeypatch):
    src = _docx(tmp_path)
    target = tmp_path / "in.pdf"
    target.write_bytes(b"%PDF stale from last run")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(DocumentConverter, "_find_soffice", lambda self: "/usr/bin/soffice")
    monkeypatch.setattr(converter_module.subprocess, "run", lambda cmd, **kw: None)

    with pytest.raises(RuntimeError):
        DocumentConverter(str(src), str(target)).convert()
    assert not target.exists()

