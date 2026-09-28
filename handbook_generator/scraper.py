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

    def parse_contacts_and_download_images(self, soup):
        """
        Parses the names of the Chair and DGS from the content,
        looks for their profile images, and downloads them.
        """
        chair_name = "Mete Soner"
        dgs_name = "Ludovic Tangpi"
        
        # Try to find contact list items to dynamically parse names
        contacts = soup.find_all(string=re.compile(r"(Chair|Director of Graduate Studies)", re.IGNORECASE))
        for contact_str in contacts:
            text = contact_str.strip()
            # Example: "Chair, Professor Mete Soner, 609-258-5130"
            if "Chair" in text and "Professor" in text:
                match = re.search(r"Professor\s+([^,]+)", text)
                if match:
                    chair_name = match.group(1).strip()
            # Example: "Director of Graduate Studies, Professor Ludovic Tangpi, 609-258-4558"
            elif "Director of Graduate Studies" in text and "Professor" in text:
                match = re.search(r"Professor\s+([^,]+)", text)
                if match:
                    dgs_name = match.group(1).strip()

        chair_img_path = os.path.join(self.media_dir, "chair_scraped.jpeg")
        dgs_img_path = os.path.join(self.media_dir, "dgs_scraped.jpeg")

        # Revert to defaults if not found
        default_chair = os.path.join(self.media_dir, "image2.jpeg")
        default_dgs = os.path.join(self.media_dir, "image3.jpeg")

        # Download chair image
        chair_url = self._find_profile_image_url(soup, chair_name)
        if chair_url:
            self._download_image(chair_url, chair_img_path)
        else:
            if os.path.exists(default_chair):
                chair_img_path = default_chair
            else:
                chair_img_path = None

        # Download dgs image
        dgs_url = self._find_profile_image_url(soup, dgs_name)
        if dgs_url:
            self._download_image(dgs_url, dgs_img_path)
        else:
            if os.path.exists(default_dgs):
                dgs_img_path = default_dgs
            else:
                dgs_img_path = None

        return {
            "chair_name": chair_name,
            "dgs_name": dgs_name,
            "chair_img_path": chair_img_path,
            "dgs_img_path": dgs_img_path
        }

    def _find_profile_image_url(self, soup, name):
        """Looks for an image tag containing the name in its alt attribute."""
        # Find first name word to be more robust
        first_name = name.split()[0]
        # Search all image tags
        imgs = soup.find_all("img")
        for img in imgs:
            alt = img.get("alt", "")
            if alt and (name.lower() in alt.lower() or first_name.lower() in alt.lower()):
                src = img.get("src", "")
                if src:
                    return urljoin(self.url, src)
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

