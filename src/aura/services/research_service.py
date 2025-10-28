import logging
import os
from typing import Dict, List

from tavily import TavilyClient


logger = logging.getLogger(__name__)


class ResearchService:
    """Research helper backed by the Tavily API."""

    def __init__(self) -> None:
        self.api_key = os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            logger.warning("TAVILY_API_KEY is not set. Configure the environment variable to enable research.")
            self.client = None
        else:
            self.client = TavilyClient(api_key=self.api_key)

    def research(self, topic: str) -> Dict[str, object]:
        query = (topic or "").strip()
        if not query:
            raise ValueError("topic must be provided for research")
        if not self.client:
            raise RuntimeError("TAVILY_API_KEY is not configured; research service is unavailable.")

        try:
            response = self.client.search(query=query, search_depth="advanced")
        except Exception as exc:
            logger.error("Tavily search failed for topic '%s': %s", query, exc, exc_info=True)
            raise RuntimeError("Research service failed to complete the request.") from exc

        summary = (response or {}).get("answer") or "No summary available."
        results = (response or {}).get("results") or []

        sources: List[Dict[str, str]] = []
        for item in results:
            title = item.get("title")
            url = item.get("url")
            if title and url:
                sources.append({"title": title, "url": url})

        return {
            "summary": summary,
            "sources": sources,
        }
