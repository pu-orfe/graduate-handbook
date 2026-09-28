import os
import pytest
from bs4 import BeautifulSoup
import docx
from handbook_generator.builder import HandbookBuilder

@pytest.fixture
def sample_data():
    return {
        "chair_name": "Test Chair",
        "dgs_name": "Test DGS",
        "chair_img_path": None,
        "dgs_img_path": None
    }

@pytest.fixture
def sample_html():
    html = """
    <div class="tex2jax_process">
      <p>Welcome to the test handbook welcome letter!</p>
      <p>Go Tigers!</p>
      <h2>Ph.D. Program Requirements</h2>
      <p>Intro text.</p>
      <h3>Core courses</h3>
      <ul>
        <li>ORF 123 Course 1</li>
        <li>ORF 456 Course 2</li>
      </ul>
      <section class="cke-callout">
        <div class="cke-callout-content">
          <p>Must be approved 2 weeks prior.</p>
        </div>
      </section>
    </div>
    """
    return BeautifulSoup(html, "html.parser")

def test_builder_generates_docx(tmp_path, sample_data, sample_html):
    output_path = os.path.join(tmp_path, "output.docx")
    template_path = os.path.join(tmp_path, "template.docx")
    
    # Create a dummy template docx
    doc_temp = docx.Document()
    doc_temp.save(template_path)
    
    builder = HandbookBuilder(template_path=template_path, output_path=output_path, media_dir=str(tmp_path))
    builder.build(sample_data, sample_html, year="2026")
    
    assert os.path.exists(output_path)
    
    # Load generated document and inspect
    generated_doc = docx.Document(output_path)
    paragraphs = [p.text for p in generated_doc.paragraphs]
    
    # Check cover page text
    assert any("Ph.D. Handbook 2026" in p for p in paragraphs)
    
    # Check TOC title
    assert any("Contents" in p for p in paragraphs)
    
    # Check welcome heading
    assert any("Message from the Chair" in p for p in paragraphs)
    
    # Check body paragraph content
    assert any("welcome letter" in p for p in paragraphs)
    
    # Check heading mapping
    assert any("Ph.D. Program Requirements" in p for p in paragraphs)
    
    # Check callout message styling (contains IMPORTANT NOTICE)
    assert any("IMPORTANT NOTICE" in p for p in paragraphs)


def test_front_matter_split_into_cover_section(tmp_path, sample_data):
    """The cover keeps the template's page setup; the body gets real margins."""
    from docx.shared import Inches
    template_path = os.path.join(tmp_path, "template.docx")
    output_path = os.path.join(tmp_path, "output.docx")
    doc = docx.Document()
    doc.sections[0].top_margin = 0
    doc.sections[0].bottom_margin = 0
    doc.add_paragraph("Contents")
    for _ in range(4):
        doc.add_paragraph("")  # spacer lines that only pushed the welcome letter down
    doc.add_paragraph("Message from the Chair and Director of Graduate Studies (DGS)")
    doc.add_paragraph("Welcome to the Department.")
    doc.add_paragraph("Ph.D. Program Requirements")
    for i in range(20):
        doc.add_paragraph(f"Body paragraph {i}")
    doc.save(template_path)

    builder = HandbookBuilder(template_path=template_path, output_path=output_path, media_dir=str(tmp_path))
    built = docx.Document(template_path)
    builder._apply_page_breaks(built)
    builder._apply_page_margins(built)

    assert len(built.sections) == 2
    cover, body = built.sections
    assert cover.top_margin == 0  # the cover layout is designed around it
    assert body.top_margin == Inches(1) and body.bottom_margin == Inches(1)
    assert body._sectPr.find(docx.oxml.ns.qn("w:pgNumType")).get(docx.oxml.ns.qn("w:start")) == "2"
    texts = [p.text for p in built.paragraphs]
    welcome = texts.index("Message from the Chair and Director of Graduate Studies (DGS)")
    # Spacers gone: only the (empty) section-break paragraph sits between them.
    assert texts[texts.index("Contents") + 1:welcome] == [""]


def test_rewritten_paragraph_drops_template_links_and_gets_real_ones(tmp_path):
    """A template hyperlink used to survive a rewrite and prefix the new text."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = docx.Document()
    para = doc.add_paragraph()
    old_link = OxmlElement("w:hyperlink")
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "http://www.princeton.edu/tigerhub"
    run.append(t)
    old_link.append(run)
    para._p.append(old_link)
    para.add_run("Payroll checks are put in your mailbox.")

    web = BeautifulSoup('<li>Payroll checks are in your mailbox; see <a href="/graduate/forms">Graduate Forms</a>'
                        ' or <a href="#top">top</a>.</li>', "html.parser").li
    builder = HandbookBuilder("unused.docx", str(tmp_path / "o.docx"), str(tmp_path))
    builder._process_text_runs_in_place(para, web, clear_all=True)

    assert para.text == "Payroll checks are in your mailbox; see Graduate Forms or top."
    links = para._p.findall(qn("w:hyperlink"))
    assert len(links) == 1  # the in-page anchor stays plain text
    target = para.part.rels[links[0].get(qn("r:id"))].target_ref
    assert target == "https://orfe.princeton.edu/graduate/forms"
