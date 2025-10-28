import logging
import re
from html import unescape
from typing import Callable, Dict, List, Optional

import requests


logger = logging.getLogger(__name__)


class ResearchService:
    """Lightweight web research helper that stays decoupled from the core LLM service."""

    SEARCH_URL = "https://duckduckgo.com/html/"
    USER_AGENT = "AuraResearchService/1.0 (+https://aura.local)"
    MAX_SOURCES = 3

    def __init__(self, llm_callback: Callable[[str], str]) -> None:
        if not callable(llm_callback):
            raise ValueError("llm_callback must be callable")
        self.llm_callback = llm_callback

    def research(self, topic: str) -> Dict[str, object]:
        query = (topic or "").strip()
        if not query:
            raise ValueError("topic must be provided for research")

        search_results = self._search(query)

        documents: List[Dict[str, str]] = []
        for result in search_results[: self.MAX_SOURCES]:
            content = self._fetch_content(result["url"])
            if content:
                documents.append(
                    {
                        "title": result["title"],
                        "url": result["url"],
                        "content": content,
                    }
                )

        if not documents:
            return {
                "summary": "No credible sources were retrieved for the requested topic.",
                "sources": [],
            }

        prompt = self._build_summary_prompt(query, documents)
        try:
            summary = self.llm_callback(prompt)
        except Exception as exc:
            logger.error("LLM callback failed while summarizing research: %s", exc, exc_info=True)
            raise

        sources = [{"title": doc["title"], "url": doc["url"]} for doc in documents]
        return {"summary": summary, "sources": sources}

    def _search(self, query: str) -> List[Dict[str, str]]:
        try:
            response = requests.get(
                self.SEARCH_URL,
                params={"q": query},
                headers={"User-Agent": self.USER_AGENT},
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error("DuckDuckGo search failed: %s", exc, exc_info=True)
            return []
        return self._parse_search_results(response.text)

    def _parse_search_results(self, html: str) -> List[Dict[str, str]]:
        results: List[Dict[str, str]] = []
        pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(html or ""):
            url = unescape(match.group("url") or "")
            title_html = match.group("title") or ""
            title_text = re.sub(r"<[^>]+>", " ", title_html)
            title_text = unescape(re.sub(r"\s+", " ", title_text).strip())
            if url and title_text:
                results.append({"title": title_text, "url": url})
        return results

    def _fetch_content(self, url: str) -> Optional[str]:
        try:
            response = requests.get(
                url,
                headers={"User-Agent": self.USER_AGENT},
                timeout=10,
            )
            response.raise_for_status()
            text = response.text
        except Exception as exc:
            logger.debug("Skipping research source %s due to fetch error: %s", url, exc)
            return None

        cleaned = re.sub(
            r"<script.*?>.*?</script>",
            " ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r"<style.*?>.*?</style>",
            " ",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return None
        # Trim to avoid sending massive payloads to the LLM callback.
        return cleaned[:4000]

    def _build_summary_prompt(self, topic: str, documents: List[Dict[str, str]]) -> str:
        snippets = []
        for idx, doc in enumerate(documents, start=1):
            snippets.append(
                f"Source {idx}:\nTitle: {doc['title']}\nURL: {doc['url']}\nContent: {doc['content']}"
            )
        content_block = "\n\n".join(snippets)
        return (
            f"You are a research analyst. Summarize the findings about '{topic}'. "
            "Highlight consensus and disagreements, mention notable data points, "
            "and keep the tone concise and factual.\n\n"
            f"{content_block}"
        )
