"""
Smart Context Manager module.

Provides intelligent context loading with semantic similarity,
dependency analysis, and token budget management.
"""

from .context_manager import ContextManager
from .relevance_scorer import RelevanceScorer
from .dependency_analyzer import DependencyAnalyzer

__all__ = [
    "ContextManager",
    "RelevanceScorer",
    "DependencyAnalyzer",
]
