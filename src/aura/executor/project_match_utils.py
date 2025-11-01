"""Reusable helpers for project name normalization and detection."""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


logger = logging.getLogger(__name__)


COMMON_NAME_PREFIXES: Sequence[str] = ("test", "demo", "example")
MATCH_STOPWORDS: Set[str] = {
    "add",
    "update",
    "modify",
    "change",
    "create",
    "build",
    "generate",
    "start",
    "scaffold",
    "bootstrap",
    "make",
    "please",
    "the",
    "a",
    "an",
    "to",
    "with",
    "using",
    "use",
    "for",
    "on",
    "in",
    "of",
    "and",
    "new",
    "project",
    "app",
    "application",
    "module",
    "code",
    "feature",
}
EDIT_KEYWORDS: Set[str] = {
    "add",
    "update",
    "modify",
    "change",
    "enhance",
    "extend",
    "fix",
    "improve",
    "refine",
    "refactor",
    "patch",
}
CREATION_KEYWORDS: Set[str] = {
    "create",
    "build",
    "generate",
    "start",
    "scaffold",
    "bootstrap",
    "make",
    "init",
    "initialize",
    "launch",
    "new",
}


def normalize_for_match(text: str) -> str: return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def tokenize_for_match(text: str) -> List[str]:
    return [token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if token and token not in MATCH_STOPWORDS]


def filter_stopwords(tokens: Iterable[str]) -> List[str]: return [token for token in tokens if token not in MATCH_STOPWORDS]


def strip_common_prefix_tokens(tokens: Iterable[str]) -> List[str]:
    token_list = list(tokens)
    idx = 0
    while idx < len(token_list) and token_list[idx] in COMMON_NAME_PREFIXES:
        idx += 1
    return token_list[idx:]


def canonicalize_tokens(tokens: Iterable[str]) -> Set[str]:
    canonical: Set[str] = set()
    for token in tokens:
        canonical.add(token)
        canonical.add(singularize_token(token))
    return {token for token in canonical if token}


def singularize_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def sequence_score(tokens_a: Iterable[str], tokens_b: Iterable[str]) -> float:
    collapsed_a = "".join(tokens_a)
    collapsed_b = "".join(tokens_b)
    if not collapsed_a or not collapsed_b:
        return 0.0
    return difflib.SequenceMatcher(None, collapsed_a, collapsed_b).ratio()


def collapse_contains(base_tokens: Iterable[str], search_tokens: Iterable[str]) -> bool:
    base = "".join(base_tokens)
    search = "".join(search_tokens)
    return bool(base and search and base in search)


def looks_like_edit_request(user_text: str) -> bool: return bool(set(normalize_for_match(user_text).split()).intersection(EDIT_KEYWORDS))


def looks_like_creation_request(user_text: str) -> bool: return bool(set(normalize_for_match(user_text).split()).intersection(CREATION_KEYWORDS))


def match_project_name(user_text: str, projects: List[Dict[str, Any]]) -> Optional[str]:
    if not user_text:
        return None

    raw_lower = user_text.lower()
    normalized_request = normalize_for_match(user_text)
    request_tokens = tokenize_for_match(user_text)
    if not request_tokens:
        return None

    focus_tokens = filter_stopwords(request_tokens) or request_tokens
    request_full = "".join(request_tokens)
    request_canonical = canonicalize_tokens(request_tokens)

    best: Optional[Dict[str, Any]] = None

    for entry in projects:
        name = (entry or {}).get("name")
        if not isinstance(name, str):
            continue

        normalized_name = normalize_for_match(name)
        collapsed_name = normalized_name.replace(" ", "") if normalized_name else ""
        slug = name.lower()

        if slug and slug in raw_lower:
            logger.debug("Matched project '%s' via direct substring.", name)
            return name
        if normalized_name and normalized_name in normalized_request:
            logger.debug("Matched project '%s' via normalized substring.", name)
            return name
        if collapsed_name and collapsed_name in request_full:
            logger.debug("Matched project '%s' via collapsed substring.", name)
            return name

        tokens = normalized_name.split() if normalized_name else []
        if not tokens:
            continue

        stripped = strip_common_prefix_tokens(tokens) or tokens
        canonical = canonicalize_tokens(stripped)
        if not canonical:
            continue

        overlap = len(canonical & request_canonical)
        required_overlap = 1 if len(canonical) <= 2 else 2
        token_score = overlap / max(len(canonical), 1)
        seq_score = sequence_score(stripped, focus_tokens)
        collapse_score = 1.0 if collapse_contains(stripped, focus_tokens) else (
            0.9 if collapse_contains(stripped, request_tokens) else 0.0
        )
        score = max(token_score, seq_score, collapse_score)

        candidate_key = (score, overlap, -len(canonical))
        if not best or candidate_key > best["key"]:
            best = {
                "name": name,
                "overlap": overlap,
                "required": required_overlap,
                "key": candidate_key,
            }

    if best and best["overlap"] >= best["required"]:
        return best["name"]
    return None
