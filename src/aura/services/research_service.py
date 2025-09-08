import logging
import re
import time
import html
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode, urlparse, parse_qs, unquote

import requests


logger = logging.getLogger(__name__)


class ResearchService:
    """
    Research Service

    End-to-end web research utility that:
    - Performs a quick web search for a topic
    - Fetches and cleans top result pages
    - Uses a fast LLM (via provided callback) to synthesize a concise, bulleted summary

    Integration notes:
    - The service is model-agnostic. The caller supplies `llm_callback(prompt) -> str`
      which is invoked for summarization using a fast model (e.g., gemini-2.5-flash).
    """

    def __init__(self, llm_callback: Callable[[str], str], user_agent: Optional[str] = None):
        self.llm_callback = llm_callback
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        })

    # ------------------------- Public API -------------------------
    def research(self, topic: str, max_results: int = 5, fetch_limit: int = 3) -> Dict:
        """
        Runs the full research pipeline and returns a concise dossier.

        Returns:
            {
              'topic': str,
              'summary': str,         # bulleted summary
              'sources': [            # top sources with titles and URLs
                { 'title': str, 'url': str }
              ]
            }
        """
        started = time.time()
        logger.info(f"ResearchService: starting research for topic: {topic}")

        results = self._search_duckduckgo(topic, max_results=max_results)
        logger.info(f"ResearchService: search returned {len(results)} results")

        # Fetch top pages
        docs: List[Dict[str, str]] = []
        for item in results[:fetch_limit]:
            url = item.get("url")
            title = item.get("title") or url
            if not url:
                continue
            try:
                text = self._fetch_page_text(url)
                if text:
                    docs.append({"title": title, "url": url, "text": text[:20000]})  # cap per-doc length
            except Exception as e:
                logger.debug(f"ResearchService: failed to fetch {url}: {e}")

        # Synthesize summary via fast LLM
        prompt = self._build_summary_prompt(topic, docs)
        summary = self._safe_llm_call(prompt)

        dossier = {
            "topic": topic,
            "summary": summary.strip() if summary else "",
            "sources": [{"title": r.get("title"), "url": r.get("url")} for r in results[:fetch_limit]]
        }

        elapsed = time.time() - started
        logger.info(f"ResearchService: completed research in {elapsed:.2f}s with {len(docs)} documents")
        return dossier

    # ------------------------- Internals -------------------------
    def _search_duckduckgo(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """
        Lightweight search using DuckDuckGo's HTML endpoint. No API key required.
        Parses top links from result anchors.
        """
        url = "https://duckduckgo.com/html/?" + urlencode({"q": query, "kl": "us-en"})
        try:
            resp = self.session.get(url, timeout=8)
            resp.raise_for_status()
            html_text = resp.text

            # Extract anchors for results (class names may change; fallback to generic pattern)
            # Typical anchor: <a class="result__a" href="/l/?kh=-1&uddg=<ENCODED_URL>">Title</a>
            anchors = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.IGNORECASE)
            results: List[Dict[str, str]] = []
            for href, title_html in anchors:
                resolved = self._resolve_ddg_redirect(href)
                title = self._strip_html(title_html)
                if resolved:
                    results.append({"title": title, "url": resolved})
                if len(results) >= max_results:
                    break
            return results
        except requests.RequestException as e:
            logger.warning(f"DuckDuckGo search failed: {e}")
            return []

    def _resolve_ddg_redirect(self, href: str) -> Optional[str]:
        """
        Resolves DuckDuckGo redirect URLs to the actual target from 'uddg' param.
        """
        if href.startswith("/l/?"):
            qs = parse_qs(urlparse(href).query)
            uddg = qs.get("uddg", [None])[0]
            if uddg:
                return unquote(uddg)
            return None
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return None

    def _fetch_page_text(self, url: str) -> str:
        """
        Fetches a web page and returns crude text by stripping tags.
        Limited-size fetch for speed and safety.
        """
        resp = self.session.get(url, timeout=8)
        resp.raise_for_status()
        content = resp.text
        # Remove script/style blocks
        content = re.sub(r"<script[\s\S]*?</script>", " ", content, flags=re.IGNORECASE)
        content = re.sub(r"<style[\s\S]*?</style>", " ", content, flags=re.IGNORECASE)
        # Drop tags
        text = re.sub(r"<[^>]+>", " ", content)
        text = html.unescape(text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _build_summary_prompt(self, topic: str, docs: List[Dict[str, str]]) -> str:
        """Creates a concise summarization prompt from fetched documents."""
        lines = [
            "You are a fast research synthesizer.",
            "Create a concise, actionable, bulleted summary for the topic below.",
            "Rules:",
            "- Write 8-12 bullets. Be specific and practical.",
            "- Prefer stable, widely accepted practices.",
            "- Reference sources inline as [1], [2], ... when relevant.",
            "- No fluff, no speculation, no marketing.",
            "",
            f"Topic: {topic}",
            "",
            "Sources:"
        ]
        for i, d in enumerate(docs, start=1):
            src_line = f"[{i}] {d.get('title') or 'Untitled'} — {d.get('url')}"
            lines.append(src_line)
        lines.append("")
        lines.append("Relevant excerpts:")
        for i, d in enumerate(docs, start=1):
            excerpt = (d.get("text") or "")[:800]
            lines.append(f"From [{i}]: {excerpt}")
        lines.append("")
        lines.append("Output: Just the bullet list, each bullet starting with '- '.")
        return "\n".join(lines)

    def _strip_html(self, html_text: str) -> str:
        text = re.sub(r"<[^>]+>", " ", html_text)
        return html.unescape(re.sub(r"\s+", " ", text).strip())

    def _safe_llm_call(self, prompt: str) -> str:
        try:
            return self.llm_callback(prompt) or ""
        except Exception as e:
            logger.error(f"ResearchService LLM summarization failed: {e}")
            return ""

