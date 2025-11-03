"""
Persistent image attachment storage for Aura's UI.

This service isolates all concerns related to writing and reading user supplied
images that originate from the command deck. UI components interact with this
module via its explicit save/load contract so the rest of the system remains
agnostic to filesystem details.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class ImageStorageService:
    """
    Persist and retrieve user provided images for later rendering.

    Responsibilities:
    - Decode base64 image payloads emitted by UI components.
    - Persist the binary content under a managed cache directory.
    - Retrieve stored images as base64 payloads for display widgets.
    - Enforce a retention limit so the cache cannot grow unbounded.
    """

    _EXTENSION_BY_MIME = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
    }

    _MIME_BY_EXTENSION = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }

    def __init__(self, storage_dir: Path, retention_limit: int = 200) -> None:
        """
        Args:
            storage_dir: Directory that will hold cached images.
            retention_limit: Maximum number of files to keep on disk.
        """
        self._storage_dir = storage_dir
        self._retention_limit = max(retention_limit, 0)
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.error("Unable to ensure image cache directory %s: %s", storage_dir, exc, exc_info=True)
            raise

    def save_image(self, base64_data: str, mime_type: str) -> Optional[str]:
        """
        Persist an image payload and return its filesystem reference.

        Args:
            base64_data: ASCII base64 encoded image bytes.
            mime_type: Reported MIME type of the image.

        Returns:
            POSIX style string path to the stored file, or None if save failed.
        """
        if not base64_data:
            logger.warning("Empty image payload received; skipping save.")
            return None

        try:
            image_bytes = base64.b64decode(base64_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            logger.error("Failed to decode base64 image payload: %s", exc, exc_info=True)
            return None

        extension = self._extension_by_mime(mime_type)
        filename = self._build_filename(extension)
        path = self._storage_dir / filename

        try:
            path.write_bytes(image_bytes)
        except OSError as exc:
            logger.error("Failed to write image to %s: %s", path, exc, exc_info=True)
            return None

        self._enforce_retention()
        return path.as_posix()

    def load_image(self, reference: str) -> Optional[Dict[str, str]]:
        """
        Load a stored image and return it as an inline base64 payload.

        Args:
            reference: Absolute path or relative path (from cache root).

        Returns:
            A dict containing base64 data, mime_type, and normalized paths,
            or None when the reference could not be resolved.
        """
        if not reference:
            return None

        path = self._resolve_reference(reference)
        if path is None or not path.exists():
            logger.debug("Image reference %s could not be resolved.", reference)
            return None

        try:
            image_bytes = path.read_bytes()
        except OSError as exc:
            logger.error("Failed to read image from %s: %s", path, exc, exc_info=True)
            return None

        base64_payload = base64.b64encode(image_bytes).decode("ascii")
        mime_type = self._mime_for_extension(path.suffix.lstrip("."))

        return {
            "base64_data": base64_payload,
            "mime_type": mime_type,
            "path": path.as_posix(),
            "relative_path": self._relative_reference(path),
        }

    def _build_filename(self, extension: str) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        random_token = uuid4().hex[:8]
        safe_extension = extension or "bin"
        return f"{timestamp}_{random_token}.{safe_extension}"

    def _extension_by_mime(self, mime_type: str) -> str:
        if mime_type in self._EXTENSION_BY_MIME:
            return self._EXTENSION_BY_MIME[mime_type]
        logger.debug("Unknown mime type %s; defaulting to png extension.", mime_type)
        return "png"

    def _mime_for_extension(self, extension: str) -> str:
        normalized = (extension or "").lower()
        return self._MIME_BY_EXTENSION.get(normalized, "application/octet-stream")

    def _resolve_reference(self, reference: str) -> Optional[Path]:
        path = Path(reference)
        if not path.is_absolute():
            path = self._storage_dir / reference
        try:
            return path.resolve(strict=False)
        except OSError as exc:
            logger.debug("Failed to resolve reference %s: %s", reference, exc, exc_info=True)
            return None

    def _relative_reference(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(self._storage_dir.resolve())
            return relative.as_posix()
        except Exception:
            # Fallback to filename only if path is outside storage root.
            return path.name

    def _enforce_retention(self) -> None:
        if self._retention_limit <= 0:
            return

        try:
            files = sorted(
                (file for file in self._storage_dir.iterdir() if file.is_file()),
                key=lambda candidate: candidate.stat().st_mtime,
            )
        except Exception as exc:
            logger.debug("Unable to enforce retention for image cache: %s", exc, exc_info=True)
            return

        excess = len(files) - self._retention_limit
        if excess <= 0:
            return

        for stale in files[:excess]:
            try:
                stale.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug("Failed to remove stale image %s: %s", stale, exc, exc_info=True)
