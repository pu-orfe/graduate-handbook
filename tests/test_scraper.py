import pytest
from bs4 import BeautifulSoup
from handbook_generator.scraper import HandbookScraper

HTML_CONTENT = """
<html>
  <head>
    <title>Graduate Handbook | ORFE</title>
  </head>
  <body>
    <div class="field--name-field-ps-body">
      <div class="tex2jax_process">
        <p>Welcome to the Department...</p>
        <div class="field field--name-field-ps-featured-image">
          <img src="/sites/default/files/picture-mete.jpg" alt="Mete Soner" />
          <img src="/sites/default/files/picture-ludovic.jpg" alt="Ludovic Tangpi" />
        </div>
        <h2>Ph.D. Program Requirements</h2>
        <ul>
          <li>ORF 522 Linear & Nonlinear Optimization (Fall)</li>
        </ul>
        <ul>
          <li>Chair, Professor Mete Soner, 609-258-5130</li>
          <li>Director of Graduate Studies, Professor Ludovic Tangpi, 609-258-4558</li>
        </ul>
      </div>
    </div>
  </body>
</html>
"""

def test_scraper_parse_names_and_finds_images(mocker, tmp_path):
    scraper = HandbookScraper(url="https://example.com/handbook", media_dir=str(tmp_path))
    soup = BeautifulSoup(HTML_CONTENT, "html.parser")
    
    # Mock download image to not hit network
    mocker.patch.object(scraper, "_download_image", return_value=True)
    
    data = scraper.parse_contacts_and_download_images(soup)
    
    assert data["chair_name"] == "Mete Soner"
    assert data["dgs_name"] == "Ludovic Tangpi"
    
    # Assert that find profile image urls resolves correctly
    url_chair = scraper._find_profile_image_url(soup, "Mete Soner")
    url_dgs = scraper._find_profile_image_url(soup, "Ludovic Tangpi")
    
    assert url_chair == "https://example.com/sites/default/files/picture-mete.jpg"
    assert url_dgs == "https://example.com/sites/default/files/picture-ludovic.jpg"

def test_extract_main_content(tmp_path):
    scraper = HandbookScraper(url="https://example.com/handbook", media_dir=str(tmp_path))
    soup = BeautifulSoup(HTML_CONTENT, "html.parser")
    
    content = scraper.extract_main_content(soup)
    assert content is not None
    assert "Welcome to the Department" in content.text


def test_parse_headers_formats():
    from handbook_generator.scraper import parse_headers
    assert parse_headers("") == {}
    assert parse_headers(None) == {}
    assert parse_headers('{"X-One": "1", "X-Two": 2}') == {"X-One": "1", "X-Two": "2"}
    assert parse_headers("X-One: 1\n\nX-Two:  a:b ") == {"X-One": "1", "X-Two": "a:b"}


def test_parse_headers_rejects_garbage():
    from handbook_generator.scraper import parse_headers
    with pytest.raises(ValueError):
        parse_headers("no colon here")
    with pytest.raises(ValueError):
        parse_headers('["not", "an", "object"]')


def test_scraper_sends_headers_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_HEADERS", "X-Test-Header: yes")
    scraper = HandbookScraper(url="https://example.com/handbook", media_dir=str(tmp_path))
    assert scraper.headers["X-Test-Header"] == "yes"
    assert "User-Agent" in scraper.headers

    monkeypatch.delenv("SCRAPER_HEADERS")
    scraper = HandbookScraper(url="https://example.com/handbook", media_dir=str(tmp_path))
    assert set(scraper.headers) == {"User-Agent"}


def _cf_encode(address, key=0x4C):
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in address)


def test_cloudflare_emails_are_decoded():
    from handbook_generator.scraper import decode_cloudflare_emails
    enc = _cf_encode("klupinac@princeton.edu")
    enc_q = _cf_encode("klupinac@princeton.edu?subject=FPO")
    soup = BeautifulSoup(
        f'<p>ext 8-4018, <a href="/cdn-cgi/l/email-protection#{enc}">'
        f'<span class="__cf_email__" data-cfemail="{enc}">[email&#160;protected]</span></a></p>'
        f'<p><a href="/cdn-cgi/l/email-protection#{enc_q}">submit the request</a></p>',
        "html.parser",
    )
    assert decode_cloudflare_emails(soup) == 3
    links = soup.find_all("a")
    assert links[0]["href"] == "mailto:klupinac@princeton.edu"
    assert links[0].get_text() == "klupinac@princeton.edu"
    assert links[1]["href"] == "mailto:klupinac@princeton.edu?subject=FPO"
    assert links[1].get_text() == "submit the request"  # real link text is kept
    assert "protected" not in soup.get_text()


def test_fixture_page_has_no_email_placeholders_after_decoding():
    import os
    from handbook_generator.scraper import decode_cloudflare_emails
    path = os.path.join(os.path.dirname(__file__), "fixtures", "handbook.html")
    soup = BeautifulSoup(open(path, encoding="utf-8").read(), "html.parser")
    assert decode_cloudflare_emails(soup) >= 1
    assert "[email" not in soup.get_text() and "email-protection" not in str(soup)
    assert "klupinac@princeton.edu" in soup.get_text()
