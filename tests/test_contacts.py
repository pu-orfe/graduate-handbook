"""Chair/DGS detection, modelled on the live page's markup (September 2026).

The live page listed Amir Ali Ahmadi as Department Chair on the profile cards
while its Important Contacts list still said "Chair, Professor Mete Soner";
the old scraper read only the list, and shipped the wrong chair and photo.
"""
import pytest
from bs4 import BeautifulSoup
from handbook_generator.scraper import HandbookScraper


def card(name, href, img_alt, img_src, position, role=None):
    role_html = (
        f'<div class="field field--name-field-ps-people-role field__item">{role}</div>' if role else ""
    )
    img_html = f'<a href="{href}"><img alt="{img_alt}" src="{img_src}"/></a>' if img_src else ""
    return f"""
    <div class="content-list-item feature-is-3x4" role="listitem">
      <div class="field field--name-field-ps-featured-image field__item">{img_html}</div>
      <div class="content-list-item-details">
        <span class="field field--name-title"><a href="{href}">{name}</a></span>
        <div class="field field--name-field-ps-people-position field__item">{position}</div>
        {role_html}
      </div>
    </div>"""


AHMADI = card("Amir Ali Ahmadi", "/faculty/people/amir-ali-ahmadi", "Professor Amirali Ahmadi",
              "/files/ahmadi_portrait.jpg", "Department Chair", "Director of the OQDS Minor")
TANGPI = card("Ludovic Tangpi", "/people/ludovic-tangpi", "Ludovic Tangpi",
              "/files/picture-5412.jpg", "Associate Professor", "Director of Graduate Studies")

CONTACTS = """
<ul>
  <li>Chair, Professor {chair}, 609-258-5130</li>
  <li>Director of Graduate Studies, Professor Ludovic Tangpi, 609-258-4558</li>
</ul>"""


def page(cards, chair_in_contacts="Amir Ali Ahmadi"):
    contacts = CONTACTS.format(chair=chair_in_contacts) if chair_in_contacts else ""
    return BeautifulSoup(
        f"""<html><body>
        <h2>Message from the Chair &amp; Director of Graduate Studies (DGS)</h2>
        <div class="content-list-items" role="list">{''.join(cards)}</div>
        <p>The readers must be approved first by the ORFE department chair.</p>
        {contacts}
        </body></html>""",
        "html.parser",
    )


@pytest.fixture
def scraper(tmp_path, mocker):
    s = HandbookScraper(url="https://orfe.example.edu/graduate/handbook", media_dir=str(tmp_path))
    s.downloads = []

    def fake_download(url, dest):
        s.downloads.append((url, dest))
        return True

    mocker.patch.object(s, "_download_image", side_effect=fake_download)
    return s


def test_profile_cards_identify_chair_and_dgs(scraper):
    data = scraper.parse_contacts_and_download_images(page([AHMADI, TANGPI]))

    assert data["chair_name"] == "Amir Ali Ahmadi"
    assert data["dgs_name"] == "Ludovic Tangpi"
    assert data["warnings"] == []
    # Each photo comes from its own card, not a name search elsewhere on the page.
    assert scraper.downloads == [
        ("https://orfe.example.edu/files/ahmadi_portrait.jpg", data["chair_img_path"]),
        ("https://orfe.example.edu/files/picture-5412.jpg", data["dgs_img_path"]),
    ]


def test_cards_win_over_stale_contacts_list_and_warn(scraper):
    data = scraper.parse_contacts_and_download_images(page([AHMADI, TANGPI], chair_in_contacts="Mete Soner"))

    assert data["chair_name"] == "Amir Ali Ahmadi"
    assert len(data["warnings"]) == 1
    assert "Mete Soner" in data["warnings"][0] and "Amir Ali Ahmadi" in data["warnings"][0]


def test_card_order_does_not_matter(scraper):
    data = scraper.parse_contacts_and_download_images(page([TANGPI, AHMADI]))
    assert (data["chair_name"], data["dgs_name"]) == ("Amir Ali Ahmadi", "Ludovic Tangpi")


def test_prose_mentioning_chair_is_not_a_contact(scraper):
    # "approved first by the ORFE department chair" must not be parsed as a name.
    data = scraper.parse_contacts_and_download_images(page([AHMADI, TANGPI], chair_in_contacts=None))
    assert data["chair_name"] == "Amir Ali Ahmadi"


def test_contacts_fallback_needs_full_name_photo(scraper):
    # No cards: fall back to the contact lines, and match photos on the full
    # name ("Amir Ali Ahmadi" == alt "Professor Amirali Ahmadi").
    imgs = """
      <img alt="Professor Amirali Ahmadi" src="/files/ahmadi.jpg"/>
      <img alt="Ludovic Tangpi" src="/files/tangpi.jpg"/>"""
    soup = BeautifulSoup(f"<html><body>{imgs}{CONTACTS.format(chair='Amir Ali Ahmadi')}</body></html>", "html.parser")

    data = scraper.parse_contacts_and_download_images(soup)
    assert data["chair_name"] == "Amir Ali Ahmadi"
    assert scraper.downloads[0][0] == "https://orfe.example.edu/files/ahmadi.jpg"


def test_first_name_alone_does_not_match_a_photo(scraper):
    soup = BeautifulSoup('<img alt="Amir Somebody-Else" src="/x.jpg"/>', "html.parser")
    assert scraper._find_profile_image_url(soup, "Amir Ali Ahmadi") is None


def test_missing_chair_is_an_error_not_a_default(scraper):
    with pytest.raises(ValueError, match="Chair"):
        scraper.parse_contacts_and_download_images(page([TANGPI], chair_in_contacts=None))


def test_missing_photo_is_an_error(scraper):
    no_photo = card("Amir Ali Ahmadi", "/a", "", None, "Department Chair")
    with pytest.raises(ValueError, match="photo"):
        scraper.parse_contacts_and_download_images(page([no_photo, TANGPI]))


def test_failed_photo_download_is_an_error(scraper, mocker):
    mocker.patch.object(scraper, "_download_image", return_value=False)
    with pytest.raises(ValueError, match="download"):
        scraper.parse_contacts_and_download_images(page([AHMADI, TANGPI]))
