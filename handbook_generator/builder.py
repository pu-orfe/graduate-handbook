import os
import re
import difflib
import copy
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from bs4 import NavigableString, Tag, BeautifulSoup

class HandbookBuilder:
    def __init__(self, template_path, output_path, media_dir="media",
                 base_url="https://orfe.princeton.edu/graduate/handbook"):
        self.template_path = template_path
        self.output_path = output_path
        self.media_dir = media_dir
        # Relative links on the page resolve against this.
        self.base_url = base_url

    def build(self, scraper_data, body_content, year=None):
        """Builds the docx document using the scraped data and content."""
        if not year:
            year = "2026"

        # Load template if exists
        if os.path.exists(self.template_path):
            doc = docx.Document(self.template_path)
        else:
            doc = docx.Document()

        # Check if template has valid structure for in-place update (at least 20 paragraphs)
        if os.path.exists(self.template_path) and len(doc.paragraphs) > 20:
            print("Template exists and has valid structure. Running in-place sync...")
            self._sync_document_in_place(doc, scraper_data, body_content, year)
        else:
            print("Template is missing or empty. Building document sequentially...")
            # Run sequential generation (fallback for unit tests / missing template)
            if os.path.exists(self.template_path):
                self._clear_document_body(doc)
            else:
                self._setup_default_page(doc)
            self._add_cover_page(doc, year)
            self._add_toc_page(doc)
            self._add_content_pages(doc, scraper_data, body_content)

        # Save document
        doc.save(self.output_path)
        print(f"Successfully generated docx at {self.output_path}")

    # --- IN-PLACE SYNC METHODS ---
    def _sync_document_in_place(self, doc, scraper_data, body_content, year):
        chair_name = scraper_data["chair_name"]
        dgs_name = scraper_data["dgs_name"]
        chair_img = scraper_data.get("chair_img_path")
        dgs_img = scraper_data.get("dgs_img_path")

        # 1. Update cover page textbox year
        self._update_textboxes(doc, year)

        # 2. Update profile images in-place (media/image2.jpeg and media/image3.jpeg)
        for rel_id, rel in doc.part.rels.items():
            if "media/image2.jpeg" in rel.target_ref and chair_img and os.path.exists(chair_img):
                with open(chair_img, "rb") as f:
                    rel.target_part._blob = f.read()
                print("Updated Chair image in-place (image2.jpeg)")
            elif "media/image3.jpeg" in rel.target_ref and dgs_img and os.path.exists(dgs_img):
                with open(dgs_img, "rb") as f:
                    rel.target_part._blob = f.read()
                print("Updated DGS image in-place (image3.jpeg)")

        # 3. Update welcome letter names line
        p_names = None
        for idx, p in enumerate(doc.paragraphs[:30]):
            if "Professor" in p.text and ("Mete" in p.text or "Ludovic" in p.text or "Soner" in p.text or "Tangpi" in p.text):
                p_names = p
                print(f"Found welcome letter names line at paragraph index P{idx:03d}")
                break
        if not p_names and len(doc.paragraphs) > 18:
            p_names = doc.paragraphs[18]

        if p_names:
            if len(p_names.runs) >= 5:
                p_names.runs[1].text = f"Professor {chair_name}"
                p_names.runs[4].text = f"Professor {dgs_name}"
            else:
                p_names.text = ""
                run0 = p_names.add_run("                       ")
                run1 = p_names.add_run(f"Professor {chair_name}")
                run1.font.bold = True
                run1.font.size = Pt(14)
                run1.font.name = "Times New Roman"
                run2 = p_names.add_run("\t\t       ")
                run2.font.size = Pt(14)
                run2.font.name = "Times New Roman"
                run3 = p_names.add_run(f"Professor {dgs_name}")
                run3.font.bold = True
                run3.font.size = Pt(14)
                run3.font.name = "Times New Roman"

        # 4. Extract and flatten HTML blocks from scraped content
        flat_web_blocks = self._flatten_html_blocks(body_content)
        print(f"Extracted and flattened to {len(flat_web_blocks)} web blocks for matching.")

        # Get logical paragraphs from the reference document
        logical_paras = self._get_logical_paragraphs(doc)
        print(f"Grouped reference document into {len(logical_paras)} logical paragraphs.")

        # 5. Synchronize logical paragraphs
        # Find start index in logical_paras corresponding to the first body paragraph
        start_lp_idx = 0
        for idx, lp in enumerate(logical_paras):
            # Welcome letter body starts with "Welcome to the Department"
            if "Welcome to the Department" in lp["text"]:
                start_lp_idx = idx
                break

        last_matched_logical_idx = start_lp_idx - 1
        
        # Track paragraph after which we insert new unmatched blocks
        lead_p = logical_paras[start_lp_idx]["lead_p"] if start_lp_idx < len(logical_paras) else None
        current_insert_p = lead_p if lead_p else (doc.paragraphs[22] if len(doc.paragraphs) > 22 else doc.paragraphs[-1])

        for wb_idx, (wb_block, wb_text) in enumerate(flat_web_blocks):
            clean_wb = self._clean_string(wb_text)
            if not clean_wb:
                continue

            # Search for matching logical paragraph in reference doc within a window
            best_ratio = 0.0
            best_lp_idx = -1

            search_start = last_matched_logical_idx + 1
            search_end = min(search_start + 40, len(logical_paras))

            for lp_idx in range(search_start, search_end):
                lp = logical_paras[lp_idx]
                if not lp["text"]:
                    continue

                clean_ref = self._clean_string(lp["text"])

                # Detect bold/italic prefix in lead paragraph
                lead_p = lp["lead_p"]
                prefix_text, prefix_runs = self._get_paragraph_prefix(lead_p)

                if prefix_text:
                    clean_ref_body = self._clean_string(lp["text"][len(prefix_text):])
                    ratio = difflib.SequenceMatcher(None, clean_ref_body, clean_wb, autojunk=False).ratio()
                    # Check if the web text matches the reference text including the prefix
                    ratio_with_prefix = difflib.SequenceMatcher(None, clean_ref, clean_wb, autojunk=False).ratio()
                    ratio = max(ratio, ratio_with_prefix)
                else:
                    ratio = difflib.SequenceMatcher(None, clean_ref, clean_wb, autojunk=False).ratio()

                if ratio > best_ratio:
                    best_ratio = ratio
                    best_lp_idx = lp_idx

            # Match criteria
            if best_ratio > 0.45:
                # Clear intermediate paragraphs. We clear non-empty ones (which were deleted on the web),
                # and we also clear empty spacer paragraphs if they are inside a list (between two list items).
                prev_lp = logical_paras[last_matched_logical_idx]
                curr_lp = logical_paras[best_lp_idx]
                
                prev_is_list = prev_lp["style"] in ['List Paragraph'] or re.match(r'^(?:ORF\s+\d{3}|•|[\d\w]+\.|[A-Z]\.\s)', prev_lp["text"])
                curr_is_list = curr_lp["style"] in ['List Paragraph'] or re.match(r'^(?:ORF\s+\d{3}|•|[\d\w]+\.|[A-Z]\.\s)', curr_lp["text"])
                delete_empty_spacers = prev_is_list and curr_is_list

                for intermediate_lp_idx in range(last_matched_logical_idx + 1, best_lp_idx):
                    inter_lp = logical_paras[intermediate_lp_idx]
                    for p in inter_lp["paragraphs"]:
                        if p._element.getparent() is not None:
                            if p.text.strip() or delete_empty_spacers:
                                p._element.getparent().remove(p._element)

                lp_target = logical_paras[best_lp_idx]
                lead_p = lp_target["lead_p"]

                # Check for callout container styling
                is_callout = False
                parent = wb_block.parent
                while parent:
                    if parent.name == 'section' and 'cke-callout' in parent.get('class', []):
                        is_callout = True
                        break
                    parent = parent.parent

                # Detect prefix to keep formatting
                prefix_text, prefix_runs = self._get_paragraph_prefix(lead_p)
                if prefix_text and clean_wb.startswith(self._clean_string(prefix_text)):
                    prefix_runs = []  # No prefix runs separate preservation, web has it

                # 1. Update lead paragraph
                if is_callout:
                    lead_p.text = ""
                    run_notice = lead_p.add_run("IMPORTANT NOTICE: ")
                    run_notice.font.name = "Times New Roman"
                    run_notice.font.size = Pt(12)
                    run_notice.font.bold = True
                    run_notice.font.color.rgb = RGBColor(255, 0, 0)
                    self._process_text_runs_in_place(lead_p, wb_block, color_override=RGBColor(255, 0, 0), clear_all=False)
                else:
                    base_format = self._extract_run_formatting(lead_p)
                    
                    color_override = None
                    if lead_p.text.startswith("Chair, Professor"):
                        color_override = RGBColor(238, 0, 0)

                    if prefix_runs:
                        # Keep prefix runs, remove the rest
                        runs_to_keep = set(prefix_runs)
                        for r in list(lead_p.runs):
                            if r not in runs_to_keep:
                                lead_p._p.remove(r._r)
                        self._remove_hyperlinks(lead_p)
                        self._process_text_runs_in_place(lead_p, wb_block, base_format=base_format, color_override=color_override, clear_all=False)
                    else:
                        # Update normal paragraph text runs ONLY if there are differences
                        if self._clean_string(lp_target["text"]) != clean_wb:
                            self._process_text_runs_in_place(lead_p, wb_block, base_format=base_format, color_override=color_override, clear_all=True)

                # 2. Delete all sub-paragraphs in the group from the XML
                for p in lp_target["paragraphs"][1:]:
                    if p._element.getparent() is not None:
                        p._element.getparent().remove(p._element)

                last_matched_logical_idx = best_lp_idx
                current_insert_p = lead_p
            else:
                # Dynamic insertion of unmatched/new block
                new_p = OxmlElement('w:p')
                current_insert_p._p.addnext(new_p)
                new_para = docx.text.paragraph.Paragraph(new_p, current_insert_p._parent)
                
                # Copy properties
                new_para.style = current_insert_p.style
                new_para.alignment = current_insert_p.alignment
                
                base_format = self._extract_run_formatting(current_insert_p)
                self._process_text_runs_in_place(new_para, wb_block, base_format=base_format, clear_all=True)
                
                print(f"Dynamically inserted unmatched web block: '{wb_text[:50]}...'")
                current_insert_p = new_para

        self._apply_page_breaks(doc)
        self._apply_page_margins(doc)

    # Major sections start on a new page; everything else flows.
    SECTION_BREAKS = (
        "ph.d. program requirements",
        "other regulations",
        "miscellaneous information",
        "important contacts",
    )

    def _apply_page_breaks(self, doc):
        """Paginates the body with layout rules instead of fixed positions.

        The template was paginated by hand in Word: runs of blank paragraphs
        push each section onto the next page, and earlier versions of this
        method added page breaks before specific mid-section sentences. Both
        depend on Word's exact line breaking. Any other renderer (LibreOffice
        in CI), or any web edit that changes a paragraph's length, shifts the
        text by a line or two, and those lines land alone on a new page.

        So, from the first section onward: blank spacer runs are collapsed to
        a single blank line, the four major sections start on a new page,
        headings stay with the text that follows them, and widow/orphan
        control is on. The cover, contents and welcome pages are left exactly
        as the template lays them out.
        """
        paragraphs = doc.paragraphs
        body_start = next(
            (i for i, p in enumerate(paragraphs) if self._section_break_target(p.text)),
            None,
        )
        if body_start is None:
            print("Warning: no major section heading found; leaving pagination as is.")
            return

        # Blank spacer runs directly before a section break are removed
        # outright (the break does their job); elsewhere one blank line stays.
        previous_blank = False
        for idx in range(body_start - 1, -1, -1):
            p = paragraphs[idx]
            if p.text.strip() or self._has_graphics(p):
                break
            p._element.getparent().remove(p._element)
        for p in paragraphs[body_start:]:
            if p._element.getparent() is None:
                continue
            blank = not p.text.strip() and not self._has_graphics(p)
            if blank and previous_blank:
                p._element.getparent().remove(p._element)
                continue
            previous_blank = blank

        paragraphs = doc.paragraphs
        body_start = next(i for i, p in enumerate(paragraphs) if self._section_break_target(p.text))
        body = paragraphs[body_start:]
        for idx, p in enumerate(body):
            fmt = p.paragraph_format
            fmt.page_break_before = None
            fmt.widow_control = True
            if self._section_break_target(p.text):
                fmt.page_break_before = True
                # A blank line left just before the heading would open the page.
                prev = body[idx - 1] if idx > 0 else None
                if prev is not None and not prev.text.strip() and not self._has_graphics(prev):
                    prev._element.getparent().remove(prev._element)
                print(f"Section starts on a new page: '{p.text.strip()[:40]}'")
            if self._is_heading(p):
                fmt.keep_with_next = True

    # The template sets top and bottom margins to zero and fakes a margin with
    # blank lines at the top of each page. The cover is designed around that
    # (its title box is positioned from the margin), so it keeps the template's
    # page setup in a section of its own; everything from the welcome letter on
    # gets a real margin, since the blank lines are gone once pagination is by rule.
    PAGE_MARGIN = Inches(1)
    WELCOME_HEADING = "message from the chair"

    def _apply_page_margins(self, doc):
        body_sectpr = doc.sections[-1]._sectPr
        welcome = next(
            (p for p in doc.paragraphs if p.text.strip().lower().startswith(self.WELCOME_HEADING)),
            None,
        )
        if welcome is not None and len(doc.sections) == 1:
            # Blank lines that only pushed the welcome letter onto page 2.
            prev = welcome._p.getprevious()
            while prev is not None and prev.tag == qn("w:p") and not self._element_text(prev).strip() \
                    and not prev.xpath('.//*[local-name()="drawing" or local-name()="pict"]'):
                earlier = prev.getprevious()
                prev.getparent().remove(prev)
                prev = earlier
            # An empty paragraph carrying the cover section's properties ends
            # the cover section; the welcome letter starts the next on a new page.
            cover_sectpr = copy.deepcopy(body_sectpr)
            breaker = OxmlElement("w:p")
            ppr = OxmlElement("w:pPr")
            ppr.append(cover_sectpr)
            breaker.append(ppr)
            welcome._p.addprevious(breaker)
            # Page 2 onward shows page numbers; "different first page" applies
            # only to the cover section now.
            for title_pg in body_sectpr.findall(qn("w:titlePg")):
                body_sectpr.remove(title_pg)
            welcome.paragraph_format.page_break_before = None
            # LibreOffice restarts page numbers at a new section; the cover is
            # one page, so the body continues at 2 (and printed page numbers
            # equal physical pages, which the contents check relies on).
            pg_num = body_sectpr.find(qn("w:pgNumType"))
            if pg_num is None:
                pg_num = OxmlElement("w:pgNumType")
                body_sectpr.find(qn("w:pgMar")).addnext(pg_num)
            pg_num.set(qn("w:start"), "2")

        body_section = doc.sections[-1]
        if not body_section.top_margin or body_section.top_margin < self.PAGE_MARGIN:
            body_section.top_margin = self.PAGE_MARGIN
        if not body_section.bottom_margin or body_section.bottom_margin < self.PAGE_MARGIN:
            body_section.bottom_margin = self.PAGE_MARGIN

    @staticmethod
    def _element_text(element):
        return "".join(t.text or "" for t in element.iter(qn("w:t")))

    def _section_break_target(self, text):
        text = re.sub(r"^\s*\d+\.?\s*", "", text.strip().lower())
        return any(text.startswith(t) for t in self.SECTION_BREAKS)

    @staticmethod
    def _has_graphics(p):
        return bool(p._p.xpath('.//*[local-name()="drawing" or local-name()="pict"]'))

    @staticmethod
    def _is_heading(p):
        """Headings are Heading-styled or all-bold short lines, never long prose."""
        text = p.text.strip()
        if not text or len(text) > 120:
            return False
        if p.style is not None and p.style.name.startswith("Heading"):
            return True
        runs = [r for r in p.runs if r.text.strip()]
        return bool(runs) and all(r.bold for r in runs)

    def _update_textboxes(self, doc, year):
        tb_count = 0
        for p_idx, p in enumerate(doc.paragraphs):
            txbx_elements = p._p.xpath('.//*[local-name()="txbxContent"]')
            if txbx_elements:
                for txbx in txbx_elements:
                    t_elements = txbx.xpath('.//*[local-name()="t"]')
                    if t_elements:
                        tb_count += 1
                        full_text = "".join(t.text for t in t_elements if t.text)
                        updated_text = re.sub(r'20\d{2}', str(year), full_text)
                        t_elements[0].text = updated_text
                        for t in t_elements[1:]:
                            t.getparent().remove(t)
                        print(f"Updated cover page textbox {tb_count} to: '{updated_text}'")

    def _clean_string(self, s):
        return re.sub(r'[\s\xa0\t\-\:\,\.\•\*\-\(\)]+', ' ', s).strip().lower()

    def _get_paragraph_prefix(self, p):
        prefix_runs = []
        prefix_text = ""
        for r in p.runs:
            if r.bold or r.italic or (not r.text.strip()):
                prefix_runs.append(r)
                prefix_text += r.text
                if re.search(r'[\.\:\t]\s*$', r.text):
                    break
            else:
                break
        if prefix_text and len(prefix_text) < 50 and re.search(r'[\.\:\t]', prefix_text):
            return prefix_text, prefix_runs
        return None, []

    def _extract_run_formatting(self, p):
        if not p.runs:
            return {}
        r = p.runs[0]
        return {
            "name": r.font.name,
            "size": r.font.size,
            "color": r.font.color.rgb if r.font.color else None,
            "bold": r.font.bold,
            "italic": r.font.italic
        }

    def _process_text_runs_in_place(self, doc_para, html_element, base_format=None, color_override=None, clear_all=True):
        if clear_all:
            for r in list(doc_para.runs):
                doc_para._p.remove(r._r)
            # Hyperlinks are not runs; left behind, the template's old link
            # text ends up glued to the front of the new web text.
            self._remove_hyperlinks(doc_para)
        
        if base_format is None:
            base_format = {}

        def apply_formatting(run, bold=False, italic=False, underline=False, color=None):
            if base_format.get("name"):
                run.font.name = base_format["name"]
            if base_format.get("size"):
                run.font.size = base_format["size"]
            if base_format.get("color"):
                run.font.color.rgb = base_format["color"]
                
            if bold or base_format.get("bold"):
                run.font.bold = True
            if italic or base_format.get("italic"):
                run.font.italic = True
            if underline:
                run.font.underline = True
            if color:
                run.font.color.rgb = color
            elif color_override:
                run.font.color.rgb = color_override

        def process_node(node, bold=False, italic=False):
            if isinstance(node, NavigableString):
                text = str(node)
                if text:
                    run = doc_para.add_run(text)
                    apply_formatting(run, bold=bold, italic=italic)
            elif isinstance(node, Tag):
                if node.name in ['strong', 'b']:
                    for child in node.children:
                        process_node(child, bold=True, italic=italic)
                elif node.name in ['em', 'i']:
                    for child in node.children:
                        process_node(child, bold=bold, italic=True)
                elif node.name == 'a':
                    url = self._link_url(node.get('href', ''))
                    for child in node.children:
                        if isinstance(child, NavigableString):
                            run = doc_para.add_run(str(child))
                            apply_formatting(run, bold=bold, italic=italic, underline=True, color=RGBColor(0, 0, 255))
                            if url:
                                self._wrap_in_hyperlink(doc_para, run, url)
                        else:
                            process_node(child, bold=bold, italic=italic)
                elif node.name == 'br':
                    doc_para.add_run('\n')
                else:
                    for child in node.children:
                        process_node(child, bold=bold, italic=italic)

        for child in html_element.children:
            process_node(child)

    def _link_url(self, href):
        """Absolute URL for a web link, or None for in-page anchors."""
        from urllib.parse import urljoin
        href = (href or "").strip()
        if not href or href.startswith("#"):
            return None
        return urljoin(self.base_url, href)

    @staticmethod
    def _wrap_in_hyperlink(doc_para, run, url):
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        rel_id = doc_para.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), rel_id)
        run._r.addprevious(link)
        link.append(run._r)

    @staticmethod
    def _remove_hyperlinks(doc_para):
        for link in doc_para._p.findall(qn("w:hyperlink")):
            doc_para._p.remove(link)

    @staticmethod
    def _flatten_html_blocks(container):
        # Go inside tex2jax_process if it exists
        inner_container = container.find('div', class_='tex2jax_process') or container
        
        flat_blocks = []
        
        def process_element(element):
            if not element.name:
                return
                
            if element.name in ['ul', 'ol']:
                # Direct li children
                for li in element.find_all('li', recursive=False):
                    li_clone = copy.copy(li)
                    # Remove nested lists
                    for sublist in li_clone.find_all(['ul', 'ol']):
                        sublist.decompose()
                    
                    flat_blocks.append((li, li_clone.get_text().strip()))
                    
                    # Recursively process nested lists
                    for sublist in li.find_all(['ul', 'ol'], recursive=False):
                        process_element(sublist)
            elif element.name == 'section':
                content_div = element.find('div', class_='cke-callout-content') or element
                for p in content_div.find_all('p', recursive=False):
                    flat_blocks.append((p, p.get_text().strip()))
            else:
                flat_blocks.append((element, element.get_text().strip()))

        for child in inner_container.children:
            if child.name:
                process_element(child)
                
        return flat_blocks

    def _get_logical_paragraphs(self, doc):
        logical_paras = []
        n = len(doc.paragraphs)
        visited = set()
        
        for i in range(n):
            if i in visited:
                continue
                
            p = doc.paragraphs[i]
            text = p.text.strip()
            if not text:
                logical_paras.append({
                    "lead_p": p,
                    "paragraphs": [p],
                    "text": "",
                    "style": p.style.name if p.style else "Normal"
                })
                continue
                
            is_list = p.style.name in ['List Paragraph'] or \
                      re.match(r'^(?:ORF\s+\d{3}|•|[\d\w]+\.|[A-Z]\.\s)', text) or \
                      re.search(r'\d{3}-\d{3}-\d{4}', text) or \
                      "@" in text
                      
            is_heading = p.style.name.startswith("Heading")
            
            if is_list or is_heading:
                logical_paras.append({
                    "lead_p": p,
                    "paragraphs": [p],
                    "text": text,
                    "style": p.style.name if p.style else "Normal"
                })
                continue
                
            group_paras = [p]
            joined_text = text
            
            j = i + 1
            while j < n:
                # Terminal punctuation check
                if re.search(r'[\.\!\?\:]\s*$', joined_text):
                    break
                    
                p_next = doc.paragraphs[j]
                text_next = p_next.text.strip()
                if not text_next:
                    break
                    
                if p_next.style.name != p.style.name:
                    break
                    
                next_is_list = p_next.style.name in ['List Paragraph'] or \
                               re.match(r'^(?:ORF\s+\d{3}|•|[\d\w]+\.|[A-Z]\.\s)', text_next) or \
                               re.search(r'\d{3}-\d{3}-\d{4}', text_next) or \
                               "@" in text_next
                if next_is_list:
                    break
                    
                # Merge only if next starts with lowercase
                if not text_next[0].islower():
                    break
                    
                group_paras.append(p_next)
                joined_text += " " + text_next
                visited.add(j)
                j += 1
                
            logical_paras.append({
                "lead_p": p,
                "paragraphs": group_paras,
                "text": joined_text,
                "style": p.style.name if p.style else "Normal"
            })
            
        return logical_paras

    # --- FALLBACK SEQUENTIAL GENERATION METHODS ---
    def _clear_document_body(self, doc):
        for p in list(doc.paragraphs):
            p_element = p._element
            p_element.getparent().remove(p_element)
        for t in list(doc.tables):
            t_element = t._element
            t_element.getparent().remove(t_element)

    def _setup_default_page(self, doc):
        section = doc.sections[0]
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.different_first_page_header_footer = True
        
        footer = section.footer
        p = footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        fldChar1 = OxmlElement('w:fldChar')
        fldChar1.set(qn('w:fldCharType'), 'begin')
        instrText = OxmlElement('w:instrText')
        instrText.set(qn('xml:space'), 'preserve')
        instrText.text = "PAGE"
        fldChar2 = OxmlElement('w:fldChar')
        fldChar2.set(qn('w:fldCharType'), 'separate')
        fldChar3 = OxmlElement('w:fldChar')
        fldChar3.set(qn('w:fldCharType'), 'end')
        run._r.append(fldChar1)
        run._r.append(instrText)
        run._r.append(fldChar2)
        run._r.append(fldChar3)

    def _add_cover_page(self, doc, year):
        doc.add_paragraph()
        doc.add_paragraph()
        logo_path = os.path.join(self.media_dir, "image1.png")
        p_logo = doc.add_paragraph()
        p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if os.path.exists(logo_path):
            run = p_logo.add_run()
            run.add_picture(logo_path, width=Inches(5.23), height=Inches(1.43))
        doc.add_paragraph()
        doc.add_paragraph()
        p_title = doc.add_paragraph()
        p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_title.paragraph_format.space_before = Pt(80)
        p_title.paragraph_format.space_after = Pt(20)
        run_title = p_title.add_run(f"Ph.D. Handbook {year}")
        run_title.font.name = "Arial"
        run_title.font.size = Pt(28)
        run_title.font.bold = True
        run_title.font.color.rgb = RGBColor(0, 0, 0)
        doc.add_paragraph()
        doc.add_paragraph()
        doc.add_page_break()

    def _add_toc_page(self, doc):
        p_title = doc.add_paragraph()
        run_title = p_title.add_run("Contents")
        run_title.font.name = "Cambria"
        run_title.font.size = Pt(14)
        run_title.font.bold = True
        run_title.font.color.rgb = RGBColor(54, 95, 145)
        p_space = doc.add_paragraph()
        p_space.add_run("   \t\t\t\t\t\t\t\t          ")
        p_toc = doc.add_paragraph()
        run_toc = p_toc.add_run()
        fldChar1 = OxmlElement('w:fldChar')
        fldChar1.set(qn('w:fldCharType'), 'begin')
        instrText = OxmlElement('w:instrText')
        instrText.set(qn('xml:space'), 'preserve')
        instrText.text = 'TOC \\o "1-3" \\h \\z \\u'
        fldChar2 = OxmlElement('w:fldChar')
        fldChar2.set(qn('w:fldCharType'), 'separate')
        fldChar3 = OxmlElement('w:fldChar')
        fldChar3.set(qn('w:fldCharType'), 'end')
        run_toc._r.append(fldChar1)
        run_toc._r.append(instrText)
        run_toc._r.append(fldChar2)
        run_toc._r.append(fldChar3)
        doc.add_page_break()

    def _add_content_pages(self, doc, scraper_data, body_content):
        """Parses HTML and builds the rest of the document content."""
        chair_name = scraper_data["chair_name"]
        dgs_name = scraper_data["dgs_name"]
        chair_img = scraper_data["chair_img_path"]
        dgs_img = scraper_data["dgs_img_path"]

        # Welcome Section
        p_welcome_hdr = doc.add_paragraph()
        self._set_style(p_welcome_hdr, "Heading 2")
        p_welcome_hdr.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_hdr = p_welcome_hdr.add_run("Message from the Chair and Director of Graduate Studies (DGS)")
        run_hdr.font.size = Pt(14)

        doc.add_paragraph() # spacing

        # Names line
        p_names = doc.add_paragraph()
        p_names.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_names_format = p_names.paragraph_format
        p_names_format.space_after = Pt(12)
        
        run_names = p_names.add_run(f"                       Professor {chair_name}\t\t       Professor {dgs_name}")
        run_names.font.bold = True
        run_names.font.size = Pt(14)

        # Profile Images line (P019)
        p_imgs = doc.add_paragraph()
        self._set_style(p_imgs, "Normal (Web)")
        p_imgs.alignment = WD_ALIGN_PARAGRAPH.LEFT
        
        # Spacer spaces
        p_imgs.add_run("                  ")
        
        # Chair Image
        if chair_img and os.path.exists(chair_img):
            r_chair = p_imgs.add_run()
            r_chair.add_picture(chair_img, width=Inches(1.93), height=Inches(2.58))
        
        # Mid spacer spaces
        p_imgs.add_run("                           ")
        
        # DGS Image
        if dgs_img and os.path.exists(dgs_img):
            r_dgs = p_imgs.add_run()
            r_dgs.add_picture(dgs_img, width=Inches(1.97), height=Inches(2.63))

        doc.add_paragraph() # spacing
        doc.add_paragraph() # spacing

        # Extract blocks from content
        container = body_content
        if len(container.find_all(recursive=False)) == 1 and container.find(recursive=False).name == 'div':
            container = container.find(recursive=False)

        blocks = [child for child in container.children if child.name]

        # First we process the welcome paragraphs (until we find first heading h2)
        idx = 0
        while idx < len(blocks):
            block = blocks[idx]
            if block.name == 'h2':
                break
            
            # Welcome paragraph processing
            if block.name == 'p':
                txt = block.get_text().strip()
                if txt:
                    p = doc.add_paragraph()
                    self._set_style(p, "Normal")
                    if txt == "Go Tigers!":
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        run = p.add_run("Go Tigers!")
                        run.font.name = "Times New Roman"
                        run.font.size = Pt(12)
                    else:
                        self._process_text_runs(p, block)
                    doc.add_paragraph() # spacing between paragraphs
            idx += 1

        # Page break after welcome section (before Ph.D. Program Requirements)
        doc.add_page_break()

        # Track the list types for special formatting
        current_section_title = ""
        current_heading_text = ""

        # Process the rest of the elements
        while idx < len(blocks):
            block = blocks[idx]
            
            if block.name in ['h2', 'h3', 'h4', 'h5']:
                text = block.get_text().strip()
                docx_text, style_name = self._map_heading(text, block.name)
                
                # Check if we should insert a page break before main Heading 1 elements
                if style_name == 'Heading 1' and docx_text in ["Other Regulations", "3. Miscellaneous Information", "4. Important Contacts"]:
                    doc.add_page_break()

                p = doc.add_paragraph()
                self._set_style(p, style_name)
                
                # Special styling for Heading 1/2 from sheet defaults
                run = p.add_run(docx_text)
                
                if style_name == 'Heading 1':
                    run.font.name = "Cambria"
                    run.font.size = Pt(14)
                    run.font.bold = True
                    run.font.color.rgb = RGBColor(54, 95, 145) # 365F91
                    p.paragraph_format.space_before = Pt(24)
                    current_section_title = docx_text
                else:
                    run.font.size = Pt(12)
                    run.font.bold = True
                    run.font.color.rgb = RGBColor(79, 129, 189) # 4F81BD
                    p.paragraph_format.space_before = Pt(10)
                
                current_heading_text = docx_text
                doc.add_paragraph() # spacing

            elif block.name == 'p':
                # Check if callout section
                is_callout = False
                parent = block.parent
                while parent:
                    if parent.name == 'section' and 'cke-callout' in parent.get('class', []):
                        is_callout = True
                        break
                    parent = parent.parent

                txt = block.get_text().strip()
                if txt:
                    p = doc.add_paragraph()
                    
                    # Determine styling
                    if is_callout:
                        # Callouts are styled as Red bold/normal
                        self._set_style(p, "Normal")
                        run_notice = p.add_run("IMPORTANT NOTICE: ")
                        run_notice.font.name = "Times New Roman"
                        run_notice.font.size = Pt(12)
                        run_notice.font.bold = True
                        run_notice.font.color.rgb = RGBColor(255, 0, 0) # Red
                        
                        # Process rest of paragraph
                        self._process_text_runs(p, block, color=RGBColor(255, 0, 0))
                    else:
                        self._set_style(p, "Normal")
                        # Justify standard body paragraphs if they are long
                        if len(txt) > 150:
                            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                        
                        # Special check for contact names inside Important Contacts
                        if current_section_title == "4. Important Contacts":
                            # Match contact styling
                            if "Chair" in txt:
                                self._process_text_runs(p, block, color=RGBColor(238, 0, 0)) # Red
                            else:
                                self._process_text_runs(p, block)
                        else:
                            self._process_text_runs(p, block)

                    doc.add_paragraph() # spacing

            elif block.name in ['ul', 'ol']:
                # Detect special list styling based on preceding heading text
                is_core_courses = "core courses" in current_heading_text.lower() or "core courses" in current_section_title.lower()
                is_general_outcomes = "possible outcomes of the general exam" in current_heading_text.lower()
                is_qualifying_reqs = "requirements for passing ph.d. qualifying" in current_heading_text.lower()

                items = block.find_all('li')
                for l_idx, item in enumerate(items):
                    if is_core_courses:
                        # Format as normal paragraph with tabs
                        txt = item.get_text().strip()
                        parts = re.split(r'\s+', txt, maxsplit=1)
                        if len(parts) == 2:
                            line = f"{parts[0]}\t{parts[1]}"
                        else:
                            line = txt
                        
                        p = doc.add_paragraph()
                        self._set_style(p, "Normal")
                        run = p.add_run(line)
                        run.font.name = "Times New Roman"
                        run.font.size = Pt(12)
                    
                    elif is_general_outcomes:
                        # Format as normal paragraphs prefixed with A. B. C.
                        prefix = chr(ord('A') + l_idx) + ". "
                        p = doc.add_paragraph()
                        self._set_style(p, "Normal")
                        run_pref = p.add_run(prefix)
                        run_pref.font.name = "Times New Roman"
                        run_pref.font.size = Pt(12)
                        self._process_text_runs(p, item)
                    
                    elif is_qualifying_reqs:
                        # Format as normal paragraphs prefixed with bullet character •
                        p = doc.add_paragraph()
                        self._set_style(p, "Default")
                        run_pref = p.add_run("• ")
                        run_pref.font.name = "Times New Roman"
                        run_pref.font.size = Pt(12)
                        self._process_text_runs(p, item)
                    
                    elif current_section_title in ["3. Miscellaneous Information", "4. Important Contacts"]:
                        # Standard normal paragraphs
                        p = doc.add_paragraph()
                        self._set_style(p, "Normal")
                        self._process_text_runs(p, item)
                        doc.add_paragraph() # extra spacing for items
                    
                    else:
                        # Standard List Bullet items using List Paragraph style with manual bullet
                        p = doc.add_paragraph()
                        self._set_style(p, 'List Paragraph')
                        run_bullet = p.add_run("• ")
                        run_bullet.font.name = "Times New Roman"
                        run_bullet.font.size = Pt(12)
                        self._process_text_runs(p, item)
                
                doc.add_paragraph() # spacing after list

            elif block.name == 'section':
                sub_container = block
                if block.find('div', class_='cke-callout-content'):
                    sub_container = block.find('div', class_='cke-callout-content')
                for child in sub_container.children:
                    if child.name == 'p':
                        p = doc.add_paragraph()
                        self._set_style(p, "Normal")
                        run_notice = p.add_run("IMPORTANT NOTICE: ")
                        run_notice.font.name = "Times New Roman"
                        run_notice.font.size = Pt(12)
                        run_notice.font.bold = True
                        run_notice.font.color.rgb = RGBColor(255, 0, 0)
                        self._process_text_runs(p, child, color=RGBColor(255, 0, 0))
                        doc.add_paragraph()

            idx += 1

    def _process_text_runs(self, doc_para, html_element, color=None):
        """Helper to recursively process HTML tags and write corresponding runs (bold, italic, links)."""
        def process_node(node):
            if isinstance(node, NavigableString):
                text = str(node)
                if text:
                    run = doc_para.add_run(text)
                    run.font.name = "Times New Roman"
                    run.font.size = Pt(12)
                    if color:
                        run.font.color.rgb = color
            elif isinstance(node, Tag):
                if node.name in ['strong', 'b']:
                    for child in node.children:
                        if isinstance(child, NavigableString):
                            run = doc_para.add_run(str(child))
                            run.font.name = "Times New Roman"
                            run.font.size = Pt(12)
                            run.font.bold = True
                            if color:
                                run.font.color.rgb = color
                        else:
                            process_node(child)
                elif node.name in ['em', 'i']:
                    for child in node.children:
                        if isinstance(child, NavigableString):
                            run = doc_para.add_run(str(child))
                            run.font.name = "Times New Roman"
                            run.font.size = Pt(12)
                            run.font.italic = True
                            if color:
                                run.font.color.rgb = color
                        else:
                            process_node(child)
                elif node.name == 'a':
                    for child in node.children:
                        if isinstance(child, NavigableString):
                            run = doc_para.add_run(str(child))
                            run.font.name = "Times New Roman"
                            run.font.size = Pt(12)
                            run.font.underline = True
                            run.font.color.rgb = RGBColor(0, 0, 255)
                        else:
                            process_node(child)
                elif node.name == 'br':
                    doc_para.add_run('\n')
                else:
                    for child in node.children:
                        process_node(child)

        for child in html_element.children:
            process_node(child)

    def _map_heading(self, text, tag_name):
        """Maps HTML headings to the target document's exact heading text and style."""
        clean = re.sub(r'[\s\xa0\t]+', ' ', text).strip().lower()
        
        heading_map = {
            "ph.d. program requirements": ("Ph.D. Program Requirements", "Heading 1"),
            "research & thesis adviser": ("1.1\tResearch & Thesis Adviser", "Heading 2"),
            "research and thesis adviser": ("1.1\tResearch & Thesis Adviser", "Heading 2"),
            "ph.d. qualifying and general examinations": ("1.2\tPh.D. Qualifying and General Examinations", "Heading 2"),
            "annual reenrollment": ("        Annual Reenrollment", "Heading 2"),
            "dissertation and post-generals courses": ("Dissertation and Post-Generals Courses", "Heading 2"),
            "final public oral (fpo): department instructions": ("Final Public Oral (FPO): Department Instructions ", "Heading 2"),
            "other regulations": ("Other Regulations", "Heading 1"),
            "satisfactory academic progress": ("2.1 Satisfactory Academic Progress", "Heading 1"),
            "academic regulations and fraud": ("2.2 Academic Regulations/Fraud", "Heading 2"),
            "academic regulations & fraud": ("2.2 Academic Regulations/Fraud", "Heading 2"),
            "academic regulations/fraud": ("2.2 Academic Regulations/Fraud", "Heading 2"),
            "changes in course status": ("Changes in Course Status", "Heading 2"),
            "auditing courses": ("Auditing Courses", "Heading 2"),
            "teaching assistants (tas)": ("Teaching Assistants (TA’s)", "Heading 2"),
            "teaching assistants": ("Teaching Assistants (TA’s)", "Heading 2"),
            "part-time employment": ("Part-time Employment", "Heading 2"),
            "leave of absence": ("Leave of Absence", "Heading 2"),
            "holiday, vacation, and travel": ("Holiday, Vacation, and Travel", "Heading 2"),
            "holiday, vacation and travel": ("Holiday, Vacation, and Travel", "Heading 2"),
            "travel support request process": ("Travel Support Request Process", "Heading 2"),
            "miscellaneous information": ("3. Miscellaneous Information", "Heading 1"),
            "important contacts": ("4. Important Contacts", "Heading 1"),
        }
        
        if clean in heading_map:
            return heading_map[clean]
            
        for key, val in heading_map.items():
            if key in clean or clean in key:
                return val
                
        if tag_name == 'h2':
            return text, 'Heading 1'
        else:
            return text, 'Heading 2'

    def _set_style(self, paragraph, style_name):
        """Safely sets the paragraph style, falling back to 'Normal' if the style is missing."""
        try:
            paragraph.style = paragraph.part.document.styles[style_name]
        except KeyError:
            try:
                paragraph.style = paragraph.part.document.styles["Normal"]
            except KeyError:
                pass
