"""
Web Searcher module using DuckDuckGo (v5+ compatible).
Provides a simple way to get snippets from the web.
"""
import logging
import re
from duckduckgo_search import DDGS
from typing import Optional

logger = logging.getLogger("jarvis.web_searcher")

class WebSearcher:
    def __init__(self):
        # Non istanziamo più DDGS qui per evitare problemi di sessione
        pass

    def search(self, query: str, max_results: int = 3) -> Optional[str]:
        """
        Perform a web search and return a concatenated string of results.
        """
        try:
            # Pulizia mirata della query
            clean_query = re.sub(r'\bcerca online\b', '', query, flags=re.IGNORECASE).strip()
            # Se inizia con "jarvis,", pulisci anche quello
            clean_query = re.sub(r'^jarvis\s*,?\s*', '', clean_query, flags=re.IGNORECASE).strip()
            
            if not clean_query:
                logger.warning("Query di ricerca vuota dopo la pulizia.")
                return None

            logger.info("Ricerca online in corso per: '%s'", clean_query)
            
            # Uso del context manager (richiesto da DDGS v5+)
            with DDGS() as ddgs:
                results = list(ddgs.text(clean_query, max_results=max_results))
            
            if not results:
                logger.info("Nessun risultato trovato online.")
                return None
            
            formatted_results = []
            for i, r in enumerate(results, 1):
                title = r.get('title', 'Senza titolo')
                body = r.get('body', '')
                href = r.get('href', '')
                formatted_results.append(f"[{i}] {title}\nFonte: {href}\nContenuto: {body}")
            
            return "\n\n---\n\n".join(formatted_results)
            
        except Exception as e:
            logger.error("Errore nella ricerca online: %s", e)
            return None
