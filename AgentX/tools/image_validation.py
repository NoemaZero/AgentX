"""Image validation — translation of image validation in query.ts.

Handles image size validation and resizing (ImageSizeError, ImageResizeError).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ImageSizeError(Exception):
    """Raised when image exceeds size limits (translation of ImageSizeError)."""

    def __init__(self, image_path: str, size: int, max_size: int):
        self.image_path = image_path
        self.size = size
        self.max_size = max_size
        super().__init__(
            f"Image {image_path} size ({size} bytes) exceeds limit ({max_size} bytes)"
        )


class ImageResizeError(Exception):
    """Raised when image resizing fails (translation of ImageResizeError)."""

    def __init__(self, image_path: str, reason: str):
        self.image_path = image_path
        self.reason = reason
        super().__init__(f"Failed to resize image {image_path}: {reason}")


# Constants (matching TS)
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB
MAX_IMAGE_DIMENSION = 8000  # pixels


def validate_image_size(image_path: str, max_size: int = MAX_IMAGE_SIZE_BYTES) -> None:
    """Validate that image file size is within limits.

    Translation of image size validation in TS.
    Raises ImageSizeError if validation fails.
    """
    import os

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    size = os.path.getsize(image_path)
    if size > max_size:
        raise ImageSizeError(image_path, size, max_size)


def validate_image_dimensions(image_path: str, max_dim: int = MAX_IMAGE_DIMENSION) -> None:
    """Validate that image dimensions are within limits.

    Translation of image dimension validation in TS.
    Requires PIL/Pillow for dimension checking.
    """
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            width, height = img.size
            if width > max_dim or height > max_dim:
                raise ImageSizeError(
                    image_path,
                    max_dim,
                    max_dim,
                )
    except ImportError:
        logger.warning("PIL/Pillow not installed, skipping dimension validation")
    except ImageSizeError:
        raise
    except Exception as exc:
        logger.warning("Failed to validate image dimensions: %s", exc)


def resize_image_if_needed(
    image_path: str,
    max_dim: int = MAX_IMAGE_DIMENSION,
    quality: int = 85,
) -> str:
    """Resize image if dimensions exceed limits.

    Translation of image resizing logic in TS.
    Returns path to resized image (may be same as input if no resize needed).
    Raises ImageResizeError if resizing fails.
    """
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            width, height = img.size
            if width <= max_dim and height <= max_dim:
                return image_path

            # Calculate new dimensions preserving aspect ratio
            ratio = min(max_dim / width, max_dim / height)
            new_width = int(width * ratio)
            new_height = int(height * ratio)

            resized = img.resize((new_width, new_height), Image.LANCZOS)

            # Save to new file
            import os

            base, ext = os.path.splitext(image_path)
            resized_path = f"{base}_resized{ext}"

            resized.save(resized_path, quality=quality)
            return resized_path

    except ImportError:
        logger.warning("PIL/Pillow not installed, cannot resize image")
        return image_path
    except ImageResizeError:
        raise
    except Exception as exc:
        raise ImageResizeError(image_path, str(exc)) from exc


def validate_and_process_image(image_path: str) -> str:
    """Validate and process image for use in messages.

    Translation of full image validation pipeline in TS.
    Returns path to processed image (may be resized).
    """
    validate_image_size(image_path)

    try:
        from PIL import Image

        validate_image_dimensions(image_path)
    except ImportError:
        logger.warning("PIL/Pillow not installed, skipping dimension validation")

    # Attempt resize if needed
    try:
        from PIL import Image  # noqa: F401

        return resize_image_if_needed(image_path)
    except ImportError:
        logger.warning("PIL/Pillow not installed, cannot resize image")
        return image_path
