"""Fetch company websites and turn them into plain text, with an on-disk cache."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

CACHE_DIR = Path("cache/sites")
TIMEOUT = 15
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Bouncer/1.0; +https://github.com/)",
    "Accept-Language": "en-US,en;q=0.9",
}
ABOUT_HINTS = ("about", "company", "who-we-are", "our-story")


def normalize_domain(value):
    """'https://www.Acme.com/pricing' -> 'acme.com'. Returns '' if nothing usable."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    host = urlparse(value).netloc.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else ""


def fetch_html(url):
    """Return (html, final_url) or (None, None) if the page can't be loaded."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None, None
    if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", ""):
        return None, None
    return resp.text, resp.url


def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    description = meta.get("content", "") if meta else ""
    body = soup.get_text(" ", strip=True)
    text = " ".join(part for part in (title, description, body) if part)
    return re.sub(r"\s+", " ", text).strip()


def find_links(html, base_url, hints, limit):
    """Same-site links whose URL or anchor text contains one of the hint words."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    found = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"]).split("#")[0].rstrip("/")
        if urlparse(url).netloc != base_host or url in found or url == base_url.rstrip("/"):
            continue
        label = (url + " " + a.get_text(" ", strip=True)).lower()
        if any(h in label for h in hints):
            found.append(url)
        if len(found) >= limit:
            break
    return found


def fetch_homepage(domain):
    for url in ("https://" + domain, "https://www." + domain, "http://" + domain):
        html, final_url = fetch_html(url)
        if html:
            return html, final_url
    return None, None


def scrape_company(domain, max_chars=6000):
    """Homepage + about page as plain text, cached per domain.

    Returns {"ok": bool, "text": str, "pages": [urls]}. Failures are cached too, so
    re-runs stay fast; delete cache/sites/ to retry them.
    """
    cache_file = CACHE_DIR / (domain + ".json")
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    result = {"ok": False, "text": "", "pages": []}
    html, home_url = fetch_homepage(domain)
    if html:
        # Homepage gets ~60% of the budget; the about page fills the rest.
        home_text = html_to_text(html)[: int(max_chars * 0.6)]
        parts, pages = ["HOMEPAGE: " + home_text], [home_url]

        about_urls = find_links(html, home_url, ABOUT_HINTS, limit=1)
        about_urls = about_urls or [urljoin(home_url, "/about"), urljoin(home_url, "/about-us")]
        for url in about_urls:
            about_html, final = fetch_html(url)
            if about_html:
                parts.append("ABOUT PAGE: " + html_to_text(about_html))
                pages.append(final)
                break

        text = "\n\n".join(parts)[:max_chars]
        result = {"ok": len(text) > 200, "text": text, "pages": pages}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result))
    return result


def research_site(domain, max_pages=8, chars_per_page=8000):
    """Deeper crawl for one-time client setup: homepage plus product, customer,
    case-study and about pages. Not cached (it runs once per client)."""
    html, home_url = fetch_homepage(domain)
    if not html:
        raise RuntimeError("Could not load https://%s. Check the domain and try again." % domain)

    hints = ("about", "customer", "case", "stories", "story", "product", "solution",
             "industr", "platform", "pricing", "who-we-serve", "use-case", "clients")
    sections = ["PAGE %s\n%s" % (home_url, html_to_text(html)[:chars_per_page])]
    logos = logo_names(html)
    for url in find_links(html, home_url, hints, limit=max_pages - 1):
        page_html, final = fetch_html(url)
        if page_html:
            sections.append("PAGE %s\n%s" % (final, html_to_text(page_html)[:chars_per_page]))
            logos += logo_names(page_html)
    if logos:
        sections.append("LOGO / IMAGE ALT TEXT (often customer names): " + ", ".join(sorted(set(logos))))
    return "\n\n".join(sections)


def logo_names(html):
    soup = BeautifulSoup(html, "html.parser")
    names = []
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").strip()
        src = (img.get("src") or "").lower()
        if alt and len(alt) < 60 and ("logo" in alt.lower() or "logo" in src or "customer" in src):
            names.append(alt)
    return names


def text_hash(*parts):
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]
