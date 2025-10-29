import base64
import binascii
import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Iterable, Optional

from src.aura.config import ROOT_DIR


logger = logging.getLogger(__name__)


class ImageStorageService:
    """
    Persists pasted images to disk and retrieves them on demand.

    Images are written below <ROOT_DIR>/image_cache using their SHA256 hash as
    the filename, which provides deterministic deduplication for identical
    inputs. Callers interact with this service using relative paths so that
    conversation metadata stays portable across environments.
    """

    DEFAULT_SUBDIR = "image_cache"

    def __init__(self, cache_subdir: Optional[str] = None) -> None:
        subdir = cache_subdir or self.DEFAULT_SUBDIR
        self.cache_dir = ROOT_DIR / subdir
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Do not crash the UI if we cannot create the directory; log and
            # leave the service in a degraded but usable state.
            logger.error("Failed to create image cache directory at %s: %s", self.cache_dir, exc)

    def save_image(self, base64_data: str, mime_type: str) -> Optional[str]:
        """
        Persist a base64-encoded image and return the relative cache path.

        Args:
            base64_data: Raw base64 data (no data URI prefix).
            mime_type: Reported MIME type, e.g. 'image/png'.

        Returns:
            A POSIX-style relative path such as 'image_cache/abcd1234.png', or
            None if the image could not be stored.
        """
        if not base64_data:
            logger.warning("save_image called with empty data; skipping write.")
            return None

        try:
            image_bytes = base64.b64decode(base64_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            logger.error("Failed to decode base64 image data: %s", exc)
            return None

        extension = self._extension_for_mime(mime_type) or "bin"
        filename = f"{hashlib.sha256(image_bytes).hexdigest()}.{extension}"
        file_path = self.cache_dir / filename

        if not file_path.exists():
            try:
                file_path.write_bytes(image_bytes)
            except OSError as exc:
                logger.error("Failed to write image to %s: %s", file_path, exc)
                return None

        try:
            relative_path = file_path.relative_to(ROOT_DIR).as_posix()
            return relative_path
        except ValueError:
            # Should never happen, but degrade gracefully.
            logger.error("Image path %s is not inside ROOT_DIR %s", file_path, ROOT_DIR)
            return None

    def load_image(self, relative_path: str) -> Optional[dict]:
        """
        Load an image from disk and return a payload suitable for display.

        Returns:
            A dict with 'base64_data' and 'mime_type', or None if the file
            could not be read.
        """
        if not relative_path:
            return None

        file_path = ROOT_DIR / Path(relative_path)
        try:
            image_bytes = file_path.read_bytes()
        except FileNotFoundError:
            logger.warning("Requested image not found at %s", file_path)
            return None
        except OSError as exc:
            logger.error("Failed to read image from %s: %s", file_path, exc)
            return None

        encoded = base64.b64encode(image_bytes).decode("ascii")
        mime_type = self._mime_for_extension(file_path.suffix.lstrip("."))
        return {"base64_data": encoded, "mime_type": mime_type}

    def cleanup_orphaned_images(self, referenced_paths: Iterable[str]) -> None:
        """
        Remove cached images that are not present in the provided references.

        Args:
            referenced_paths: Iterable of relative paths that should be kept.
        """
        safe_paths = {
            (ROOT_DIR / Path(path)).resolve()
            for path in (referenced_paths or [])
            if path
        }

        if not self.cache_dir.exists():
            return

        try:
            for file_path in self.cache_dir.iterdir():
                if not file_path.is_file():
                    continue
                if file_path.resolve() not in safe_paths:
                    try:
                        file_path.unlink()
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        logger.error("Failed to delete cached image %s: %s", file_path, exc)
        except OSError as exc:
            logger.error("Failed to iterate image cache directory %s: %s", self.cache_dir, exc)

    @staticmethod
    def _extension_for_mime(mime_type: Optional[str]) -> Optional[str]:
        if not mime_type:
            return None
        if "/" not in mime_type:
            return None
        explicit_map = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/x-icon": "ico",
        }
        if mime_type in explicit_map:
            return explicit_map[mime_type]
        guessed = mimetypes.guess_extension(mime_type, strict=False)
        if guessed:
            return guessed.lstrip(".")
        return None

    @staticmethod
    def _mime_for_extension(extension: Optional[str]) -> str:
        if not extension:
            return "application/octet-stream"
        extension = extension.lower()
        explicit_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
            "ico": "image/x-icon",
        }
        return explicit_map.get(extension, mimetypes.types_map.get(f".{extension}", "application/octet-stream"))
