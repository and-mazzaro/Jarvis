"""
Kiwix Wikipedia client.

Queries a running kiwix-serve instance (http://localhost:8888) to retrieve
article summaries, then strips HTML via BeautifulSoup.
"""
import logging
import re
import urllib.parse

import requests
from pathlib import Path
from bs4 import BeautifulSoup

logger = logging.getLogger("jarvis.knowledge.kiwix")


class KiwixClient:
    def __init__(self, config: dict):
        self.host: str = config.get("host", "localhost")
        self.port: int = config.get("port", 8888)
        self.max_chars: int = config.get("max_article_chars", 3000)
        self.zim_path: str = config.get("zim_path", "")
        
        # Extract content ID from filename (e.g. "wiki.zim" -> "wiki")
        self.content_id = Path(self.zim_path).stem if self.zim_path else "wikipedia_it_all_nopic"
        
        self.base_url = f"http://{self.host}:{self.port}"
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "Jarvis-KiwixClient/1.0"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        """Return True if kiwix-serve is reachable."""
        try:
            r = self._session.get(self.base_url, timeout=3)
            return r.status_code < 500
        except requests.RequestException:
            return False

    def search(self, query: str) -> str:
        """
        Search Wikipedia for *query* and return a plain-text excerpt
        (up to max_article_chars characters).  Returns "" on failure.
        """
        article_url = self._find_article_url(query)
        if not article_url:
            logger.info("Kiwix: no article found for %r", query)
            return ""

        text = self._fetch_article_text(article_url)
        if not text:
            return ""

        excerpt = text[: self.max_chars]
        logger.info(
            "Kiwix: retrieved %d chars for %r from %s",
            len(excerpt),
            query,
            article_url,
        )
        return excerpt

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_article_url(self, query: str) -> str:
        """
        Use the kiwix-serve search endpoint to find the best matching article.
        Returns the full article URL or "".
        """
        # kiwix-serve search endpoint
        search_url = f"{self.base_url}/search"
        params = {"content": self.content_id, "pattern": query, "lang": "ita"}
        try:
            r = self._session.get(search_url, params=params, timeout=10)
            if r.status_code != 200:
                # Fallback: try direct article URL guess
                return self._guess_article_url(query)

            soup = BeautifulSoup(r.text, "html.parser")
            # kiwix-serve returns a list of <a> hrefs
            first_link = soup.find("a", href=True)
            if first_link:
                href = first_link["href"]
                if href.startswith("/"):
                    return self.base_url + href
                return href
        except requests.RequestException as exc:
            logger.warning("Kiwix search error: %s", exc)

        return self._guess_article_url(query)

    def _guess_article_url(self, query: str) -> str:
        """
        Attempt a direct URL based on title convention used by kiwix-serve.
        e.g. /content_id/A/Titolo_Articolo
        """
        title = "_".join(word.capitalize() for word in query.strip().split())
        return f"{self.base_url}/{self.content_id}/A/{urllib.parse.quote(title)}"

    def _fetch_article_text(self, url: str) -> str:
        """Fetch an article page and extract plain text."""
        try:
            r = self._session.get(url, timeout=15)
            if r.status_code != 200:
                return ""
            soup = BeautifulSoup(r.text, "html.parser")

            # Remove navigation, tables, infoboxes
            for tag in soup.select("table, nav, .navbox, .infobox, script, style"):
                tag.decompose()

            # Grab <p> paragraphs
            paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
            text = "\n".join(p for p in paragraphs if len(p) > 40)
            return _clean_text(text)
        except requests.RequestException as exc:
            logger.warning("Kiwix fetch error: %s", exc)
            return ""


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #

def _clean_text(text: str) -> str:
    text = re.sub(r"\[[\d\w]+\]", "", text)  # remove [1], [citation needed] etc.
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()
