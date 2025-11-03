"""
Pydantic models for Smart Context Manager.

These models represent the data structures used for intelligent context loading,
file relevance scoring, and context window management.
"""

from typing import List, Dict, Any, Optional
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class ContextMode(str, Enum):
    """Context loading mode based on intent."""
    BOOTSTRAP = "bootstrap"  # Creating new project - focus on structure & patterns
    ITERATE = "iterate"      # Modifying existing project - focus on target files & dependencies


class FileRelevance(BaseModel):
    """Represents a file's relevance to the current task."""

    file_path: str = Field(..., description="Absolute or relative path to the file")
    relevance_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Relevance score between 0.0 and 1.0"
    )
    relevance_reason: str = Field(
        default="",
        description="Human-readable reason for relevance (e.g., 'semantic match', 'dependency')"
    )
    file_size_bytes: int = Field(default=0, ge=0, description="File size in bytes")
    estimated_tokens: int = Field(default=0, ge=0, description="Estimated token count")

    def __lt__(self, other: 'FileRelevance') -> bool:
        """Enable sorting by relevance score (descending)."""
        return self.relevance_score > other.relevance_score


class DependencyInfo(BaseModel):
    """Information about a file's dependencies."""

    source_file: str = Field(..., description="File that contains the imports")
    imported_modules: List[str] = Field(
        default_factory=list,
        description="List of imported module names"
    )
    imported_files: List[str] = Field(
        default_factory=list,
        description="Resolved file paths of local imports"
    )
    external_dependencies: List[str] = Field(
        default_factory=list,
        description="External package imports (not local files)"
    )


class ContextWindow(BaseModel):
    """Represents the loaded context window with token budget tracking."""

    mode: ContextMode = Field(..., description="Context loading mode")
    user_request: str = Field(..., description="Original user request")
    loaded_files: List[FileRelevance] = Field(
        default_factory=list,
        description="Files loaded into context, sorted by relevance"
    )
    total_tokens: int = Field(default=0, ge=0, description="Total tokens in context")
    max_tokens: int = Field(default=8000, gt=0, description="Maximum allowed tokens")
    truncated: bool = Field(
        default=False,
        description="Whether context was truncated to fit budget"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata (e.g., timing, cache hits)"
    )

    @field_validator('loaded_files')
    @classmethod
    def sort_by_relevance(cls, v: List[FileRelevance]) -> List[FileRelevance]:
        """Ensure files are sorted by relevance score (descending)."""
        return sorted(v, reverse=True)

    @property
    def token_utilization(self) -> float:
        """Returns the percentage of token budget used."""
        if self.max_tokens == 0:
            return 0.0
        return min(1.0, self.total_tokens / self.max_tokens)

    @property
    def remaining_tokens(self) -> int:
        """Returns the number of tokens remaining in budget."""
        return max(0, self.max_tokens - self.total_tokens)


class SemanticSearchResult(BaseModel):
    """Result from semantic similarity search."""

    file_path: str = Field(..., description="Path to the matched file")
    similarity_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Cosine similarity score"
    )
    matched_content: Optional[str] = Field(
        default=None,
        description="Snippet of matched content"
    )


class ContextConfig(BaseModel):
    """Configuration for ContextManager."""

    max_tokens: int = Field(
        default=8000,
        gt=0,
        description="Maximum tokens allowed in context window"
    )
    min_relevance_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score to include a file"
    )
    max_files: int = Field(
        default=20,
        gt=0,
        description="Maximum number of files to load"
    )
    include_dependencies: bool = Field(
        default=True,
        description="Whether to include imported dependencies"
    )
    dependency_depth: int = Field(
        default=2,
        ge=0,
        le=5,
        description="How many levels deep to traverse dependencies"
    )
    semantic_top_k: int = Field(
        default=10,
        gt=0,
        description="Number of top semantic matches to retrieve"
    )
    bootstrap_focus: List[str] = Field(
        default_factory=lambda: [
            "README.md",
            "requirements.txt",
            "setup.py",
            "pyproject.toml",
            "__init__.py"
        ],
        description="Priority files for BOOTSTRAP mode"
    )
    iterate_focus: List[str] = Field(
        default_factory=lambda: [
            "test_*.py",
            "*_test.py",
            "tests.py"
        ],
        description="Additional files to include for ITERATE mode (e.g., tests)"
    )
