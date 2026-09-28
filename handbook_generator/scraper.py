import os
import re
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

def parse_headers(value):
    """Parses extra request headers from a JSON object or "Name: value" lines."""
    value = (value or "").strip()
    if not value:
        return {}
    if value.startswith("{"):
        headers = json.loads(value)
        if not isinstance(headers, dict):
            raise ValueError("SCRAPER_HEADERS JSON must be an object")
        return {str(k): str(v) for k, v in headers.items()}
    headers = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        name, sep, val = line.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"SCRAPER_HEADERS line is not 'Name: value': {line!r}")
        headers[name.strip()] = val.strip()
    return headers


class HandbookScraper:
    def __init__(self, url="https://orfe.princeton.edu/graduate/handbook", media_dir="media"):
        self.url = url
        self.media_dir = media_dir
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        # The site's bot protection needs an extra header. It is kept out of
        # this public repository: CI reads it from the SCRAPER_HEADERS secret,
        # local runs from .env (see README).
        self.headers.update(parse_headers(os.environ.get("SCRAPER_HEADERS", "")))
        os.makedirs(self.media_dir, exist_ok=True)

    def fetch_page(self):
        """Fetches the webpage HTML and returns a BeautifulSoup object."""
        response = requests.get(self.url, headers=self.headers, timeout=30)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    CHAIR_ROLE = re.compile(r"\b(?:Department\s+)?Chair\b", re.IGNORECASE)
    DGS_ROLE = re.compile(r"\bDirector of Graduate Studies\b", re.IGNORECASE)

    def parse_contacts_and_download_images(self, soup):
        """
        Finds the Chair and DGS and downloads their photos.

        The profile cards beside the welcome letter are the source of truth:
        each card carries a name, a position/role and the photo, so the photo
        cannot be paired with the wrong person. The "Chair, Professor X" lines
        in Important Contacts are only a fallback - they are free text and
        have gone stale on the site before. Anything not found is an error,
        never a guess: a wrong name or photo on the cover letter is worse than
        a failed run.
        """
        cards = self._parse_people_cards(soup)
        contacts = self._parse_contact_lines(soup)
        warnings = []

        people = {}
        for key, role, label in (("chair", self.CHAIR_ROLE, "Chair"),
                                 ("dgs", self.DGS_ROLE, "Director of Graduate Studies")):
            card = next((c for c in cards if any(role.search(r) for r in c["roles"])), None)
            listed = contacts.get(key)
            if card:
                name, img_url = card["name"], card["img_url"]
                if listed and self._normalize_name(listed) != self._normalize_name(name):
                    warnings.append(
                        f"The page disagrees about the {label}: the profile card says "
                        f"{name!r} but Important Contacts says {listed!r}. Using the "
                        "profile card; the contacts list on the web page needs updating."
                    )
            elif listed:
                name, img_url = listed, self._find_profile_image_url(soup, listed)
            else:
                raise ValueError(f"Could not find the {label} on the page (no profile card or contact line).")
            if not img_url:
                raise ValueError(f"Could not find a photo for the {label}, {name}.")

            img_path = os.path.join(self.media_dir, f"{key}_scraped.jpeg")
            if not self._download_image(img_url, img_path):
                raise ValueError(f"Could not download the {label}'s photo from {img_url}.")
            people[key] = (name, img_path)

        return {
            "chair_name": people["chair"][0],
            "dgs_name": people["dgs"][0],
            "chair_img_path": people["chair"][1],
            "dgs_img_path": people["dgs"][1],
            "warnings": warnings,
        }

    def _parse_people_cards(self, soup):
        """Returns [{name, roles, img_url}] for each people-list card on the page."""
        cards = []
        for item in soup.select("div.content-list-item"):
            title = item.select_one(".field--name-title")
            if not title:
                continue
            name = " ".join(title.get_text(" ").split())
            roles = [
                " ".join(f.get_text(" ").split())
                for f in item.select(".field--name-field-ps-people-position, .field--name-field-ps-people-role")
            ]
            img = item.find("img")
            img_url = urljoin(self.url, img["src"]) if img and img.get("src") else None
            if name:
                cards.append({"name": name, "roles": roles, "img_url": img_url})
        return cards

    def _parse_contact_lines(self, soup):
        """Parses "Chair, Professor X, phone" style lines into {"chair": X, "dgs": Y}."""
        found = {}
        for node in soup.find_all(string=re.compile(r"Professor", re.IGNORECASE)):
            text = " ".join(node.split())
            match = re.match(r"^(.*?),\s*Professor\s+([^,]+)", text)
            if not match:
                continue
            role, name = match.group(1), match.group(2).strip()
            if self.DGS_ROLE.search(role):
                found.setdefault("dgs", name)
            elif self.CHAIR_ROLE.search(role):
                found.setdefault("chair", name)
        return found

    @staticmethod
    def _normalize_name(name):
        return re.sub(r"[^a-z]", "", name.lower())

    def _find_profile_image_url(self, soup, name):
        """Finds an image whose alt text contains the full name.

        Compared with spaces and punctuation stripped, so "Amir Ali Ahmadi"
        matches alt="Professor Amirali Ahmadi". A first name alone is not
        enough - that is how a photo gets attached to the wrong person.
        """
        wanted = self._normalize_name(name)
        if not wanted:
            return None
        for img in soup.find_all("img"):
            if wanted in self._normalize_name(img.get("alt", "")) and img.get("src"):
                return urljoin(self.url, img["src"])
        return None

    def _download_image(self, url, dest_path):
        """Downloads an image from url to dest_path."""
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            res.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(res.content)
            return True
        except Exception as e:
            print(f"Warning: Failed to download image from {url}: {e}")
            return False

    def extract_main_content(self, soup):
        """Extracts the main body container from the parsed HTML."""
        divs = soup.find_all("div", class_="field--name-field-ps-body")
        if divs:
            return max(divs, key=lambda d: len(d.text))
        
        # Fallback to tex2jax_process
        divs = soup.find_all("div", class_="tex2jax_process")
        if divs:
            return max(divs, key=lambda d: len(d.text))
            
        return None

