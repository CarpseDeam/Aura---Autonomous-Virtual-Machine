"""
Relevance Scorer using semantic similarity.

Uses FAISS and sentence-transformers to compute file relevance scores
based on semantic similarity between the user request and file contents.
"""

import logging
import numpy as np
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from ..models.context_models import FileRelevance, SemanticSearchResult

logger = logging.getLogger(__name__)

# Try to import semantic search dependencies
try:
    from sentence_transformers import SentenceTransformer
    import faiss
    SEMANTIC_AVAILABLE = True
except ImportError:
    SEMANTIC_AVAILABLE = False
    logger.warning("sentence-transformers or faiss not available. Semantic scoring disabled.")


class RelevanceScorer:
    """
    Computes file relevance scores using semantic similarity.

    Uses sentence-transformers for embeddings and FAISS for efficient similarity search.
    Falls back to basic heuristics if semantic search is unavailable.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        use_cache: bool = True
    ):
        """
        Initialize the relevance scorer.

        Args:
            model_name: Name of the sentence-transformers model
            use_cache: Whether to cache embeddings and results
        """
        self.model_name = model_name
        self.use_cache = use_cache

        # Initialize model if available
        if SEMANTIC_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_name)
                self.embedding_dim = self.model.get_sentence_embedding_dimension()
                logger.info(f"Initialized RelevanceScorer with {model_name} (dim={self.embedding_dim})")
            except Exception as e:
                logger.error(f"Failed to load model {model_name}: {e}")
                self.model = None
                self.embedding_dim = 0
        else:
            self.model = None
            self.embedding_dim = 0

        # Caches
        self._embedding_cache: Dict[str, np.ndarray] = {}
        self._file_index: Optional[faiss.Index] = None
        self._indexed_files: List[str] = []

    def score_files(
        self,
        user_request: str,
        file_paths: List[str],
        file_contents: Optional[Dict[str, str]] = None
    ) -> List[FileRelevance]:
        """
        Score a list of files based on relevance to the user request.

        Args:
            user_request: The user's task description
            file_paths: List of file paths to score
            file_contents: Optional dict mapping file paths to their contents
                           (if not provided, files will be read from disk)

        Returns:
            List of FileRelevance objects, sorted by score (descending)
        """
        if not file_paths:
            return []

        # If semantic search is available, use it
        if self.model is not None and SEMANTIC_AVAILABLE:
            return self._score_semantic(user_request, file_paths, file_contents)
        else:
            # Fall back to heuristic scoring
            return self._score_heuristic(user_request, file_paths, file_contents)

    def _score_semantic(
        self,
        user_request: str,
        file_paths: List[str],
        file_contents: Optional[Dict[str, str]]
    ) -> List[FileRelevance]:
        """
        Score files using semantic similarity (FAISS + embeddings).

        Args:
            user_request: User's task description
            file_paths: Files to score
            file_contents: Optional file contents dict

        Returns:
            Scored and sorted FileRelevance list
        """
        try:
            # Encode the user request
            query_embedding = self.model.encode(
                [user_request],
                convert_to_numpy=True,
                show_progress_bar=False
            )[0]

            results = []

            for file_path in file_paths:
                # Get or compute file embedding
                file_embedding = self._get_file_embedding(file_path, file_contents)

                if file_embedding is None:
                    # Skip files that couldn't be processed
                    continue

                # Compute cosine similarity
                similarity = self._cosine_similarity(query_embedding, file_embedding)

                # Get file size and estimate tokens
                file_size = self._get_file_size(file_path)
                estimated_tokens = self._estimate_tokens(file_path, file_contents)

                results.append(FileRelevance(
                    file_path=file_path,
                    relevance_score=float(similarity),
                    relevance_reason="semantic match",
                    file_size_bytes=file_size,
                    estimated_tokens=estimated_tokens
                ))

            # Sort by relevance (descending)
            results.sort(reverse=True)
            return results

        except Exception as e:
            logger.error(f"Error in semantic scoring: {e}", exc_info=True)
            # Fall back to heuristic
            return self._score_heuristic(user_request, file_paths, file_contents)

    def _score_heuristic(
        self,
        user_request: str,
        file_paths: List[str],
        file_contents: Optional[Dict[str, str]]
    ) -> List[FileRelevance]:
        """
        Score files using simple heuristics (keyword matching, file patterns).

        Args:
            user_request: User's task description
            file_paths: Files to score
            file_contents: Optional file contents dict

        Returns:
            Scored and sorted FileRelevance list
        """
        # Extract keywords from request (simple tokenization)
        keywords = set(user_request.lower().split())

        results = []

        for file_path in file_paths:
            score = 0.0
            reason = []

            # Score based on file name matching
            file_name = Path(file_path).name.lower()
            file_stem = Path(file_path).stem.lower()

            # Check if keywords appear in file name
            for keyword in keywords:
                if keyword in file_name:
                    score += 0.3
                    reason.append(f"filename contains '{keyword}'")

            # Prioritize certain file types
            if file_path.endswith(('.py', '.js', '.ts', '.tsx', '.jsx')):
                score += 0.1
                reason.append("code file")

            # Read file content and check for keyword matches
            content = None
            if file_contents and file_path in file_contents:
                content = file_contents[file_path]
            else:
                content = self._read_file_safe(file_path)

            if content:
                content_lower = content.lower()
                keyword_matches = sum(1 for kw in keywords if kw in content_lower)
                if keyword_matches > 0:
                    score += min(0.5, keyword_matches * 0.1)
                    reason.append(f"{keyword_matches} keyword matches in content")

            # Normalize score to [0, 1]
            score = min(1.0, score)

            file_size = self._get_file_size(file_path)
            estimated_tokens = self._estimate_tokens(file_path, file_contents)

            results.append(FileRelevance(
                file_path=file_path,
                relevance_score=score,
                relevance_reason="; ".join(reason) if reason else "heuristic match",
                file_size_bytes=file_size,
                estimated_tokens=estimated_tokens
            ))

        # Sort by relevance (descending)
        results.sort(reverse=True)
        return results

    def build_index(
        self,
        file_paths: List[str],
        file_contents: Optional[Dict[str, str]] = None
    ) -> bool:
        """
        Build a FAISS index for fast similarity search.

        Args:
            file_paths: Files to index
            file_contents: Optional file contents dict

        Returns:
            True if index was built successfully
        """
        if not SEMANTIC_AVAILABLE or self.model is None:
            logger.warning("Cannot build index: semantic search unavailable")
            return False

        try:
            embeddings = []
            indexed_files = []

            for file_path in file_paths:
                embedding = self._get_file_embedding(file_path, file_contents)
                if embedding is not None:
                    embeddings.append(embedding)
                    indexed_files.append(file_path)

            if not embeddings:
                logger.warning("No files could be embedded")
                return False

            # Create FAISS index
            embeddings_array = np.vstack(embeddings).astype('float32')
            self._file_index = faiss.IndexFlatIP(self.embedding_dim)  # Inner product (cosine similarity)

            # Normalize embeddings for cosine similarity
            faiss.normalize_L2(embeddings_array)
            self._file_index.add(embeddings_array)
            self._indexed_files = indexed_files

            logger.info(f"Built FAISS index with {len(indexed_files)} files")
            return True

        except Exception as e:
            logger.error(f"Error building index: {e}", exc_info=True)
            return False

    def search_index(
        self,
        query: str,
        top_k: int = 10
    ) -> List[SemanticSearchResult]:
        """
        Search the FAISS index for relevant files.

        Args:
            query: Search query
            top_k: Number of top results to return

        Returns:
            List of SemanticSearchResult objects
        """
        if self._file_index is None or not self._indexed_files:
            logger.warning("Index not built. Call build_index() first.")
            return []

        try:
            # Encode query
            query_embedding = self.model.encode(
                [query],
                convert_to_numpy=True,
                show_progress_bar=False
            )[0].astype('float32').reshape(1, -1)

            # Normalize for cosine similarity
            faiss.normalize_L2(query_embedding)

            # Search
            k = min(top_k, len(self._indexed_files))
            distances, indices = self._file_index.search(query_embedding, k)

            results = []
            for idx, distance in zip(indices[0], distances[0]):
                if idx < len(self._indexed_files):
                    results.append(SemanticSearchResult(
                        file_path=self._indexed_files[idx],
                        similarity_score=float(distance),
                        matched_content=None
                    ))

            return results

        except Exception as e:
            logger.error(f"Error searching index: {e}", exc_info=True)
            return []

    def _get_file_embedding(
        self,
        file_path: str,
        file_contents: Optional[Dict[str, str]] = None
    ) -> Optional[np.ndarray]:
        """
        Get or compute the embedding for a file.

        Args:
            file_path: Path to the file
            file_contents: Optional file contents dict

        Returns:
            Embedding vector or None if failed
        """
        # Check cache
        if self.use_cache and file_path in self._embedding_cache:
            return self._embedding_cache[file_path]

        # Get file content
        if file_contents and file_path in file_contents:
            content = file_contents[file_path]
        else:
            content = self._read_file_safe(file_path)

        if not content:
            return None

        try:
            # Truncate very long files to avoid memory issues
            max_chars = 10000
            if len(content) > max_chars:
                content = content[:max_chars]

            # Encode
            embedding = self.model.encode(
                [content],
                convert_to_numpy=True,
                show_progress_bar=False
            )[0]

            # Cache
            if self.use_cache:
                self._embedding_cache[file_path] = embedding

            return embedding

        except Exception as e:
            logger.warning(f"Error encoding {file_path}: {e}")
            return None

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    def _read_file_safe(self, file_path: str) -> Optional[str]:
        """Safely read a file, returning None on failure."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.debug(f"Could not read {file_path}: {e}")
            return None

    def _get_file_size(self, file_path: str) -> int:
        """Get file size in bytes."""
        try:
            return Path(file_path).stat().st_size
        except Exception:
            return 0

    def _estimate_tokens(
        self,
        file_path: str,
        file_contents: Optional[Dict[str, str]] = None
    ) -> int:
        """
        Estimate token count for a file.

        Uses rough heuristic: 1 token ≈ 4 characters.
        """
        if file_contents and file_path in file_contents:
            content = file_contents[file_path]
        else:
            content = self._read_file_safe(file_path)

        if not content:
            return 0

        # Rough estimate: 1 token ≈ 4 characters
        return len(content) // 4

    def clear_cache(self) -> None:
        """Clear all caches and indices."""
        self._embedding_cache.clear()
        self._file_index = None
        self._indexed_files.clear()
        logger.debug("Relevance scorer cache cleared")
