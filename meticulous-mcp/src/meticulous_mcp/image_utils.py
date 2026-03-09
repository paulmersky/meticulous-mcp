"""Utility for resolving image values from various sources.

Supports data URIs (passthrough), HTTP(S) URLs (fetch + encode),
and file:// URIs (read + encode). Automatically resizes images that
would exceed the machine's 1MB request body limit.
"""

import base64
import io
import mimetypes
from urllib.parse import urlparse

import requests

# The machine's nginx enforces a 1MB (1_048_576 bytes) request body limit.
# The image is base64-encoded inside a JSON profile payload that includes
# stages, variables, etc. Reserve headroom for the rest of the payload.
MAX_B64_BYTES = 700_000  # ~700KB of base64 leaves room for the profile JSON
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20MB max input image size


def _fit_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Resize an image if its base64 encoding would exceed the size limit.

    Tries the original first, then progressively smaller resolutions.
    Converts PNG to JPEG for better compression.

    Args:
        data: Raw image bytes.
        mime: Original MIME type.

    Returns:
        Tuple of (possibly resized image bytes, final MIME type).
    """
    from PIL import Image

    b64_size = len(base64.b64encode(data))
    if b64_size <= MAX_B64_BYTES:
        return data, mime

    img = Image.open(io.BytesIO(data))
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Try progressively smaller sizes until it fits
    max_dims = [2048, 1536, 1024, 768, 512]
    for max_dim in max_dims:
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        result = buf.getvalue()

        if len(base64.b64encode(result)) <= MAX_B64_BYTES:
            return result, "image/jpeg"

    # Last resort: lowest resolution we tried
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    result = buf.getvalue()
    if len(base64.b64encode(result)) > MAX_B64_BYTES:
        raise ValueError(
            f"Image still exceeds maximum size ({MAX_B64_BYTES} base64 bytes) "
            "after all resize attempts"
        )
    return result, "image/jpeg"


def _try_fit(data: bytes, mime: str) -> tuple[bytes, str]:
    """Attempt to fit the image, falling back to original if Pillow is unavailable."""
    try:
        return _fit_image(data, mime)
    except ImportError:
        # Pillow not installed — return as-is, let the backend reject if too large
        return data, mime


def resolve_image(value: str) -> str:
    """Resolve an image value to a base64 data URI.

    Fetches the image from the given source, automatically resizes if it would
    exceed the machine's upload limit, and returns a base64 data URI.

    Args:
        value: One of:
            - data:image/...;base64,... (passthrough)
            - http:// or https:// URL (fetched and encoded)
            - file:///path (read from disk and encoded)

    Returns:
        A data URI string (data:image/...;base64,...).

    Raises:
        ValueError: If the value format is unrecognized or the fetch/read fails.
    """
    if value.startswith("data:"):
        return value

    parsed = urlparse(value)

    if parsed.scheme in ("http", "https"):
        try:
            resp = requests.get(value, timeout=30, allow_redirects=False)
            if resp.is_redirect or resp.is_permanent_redirect:
                raise ValueError(
                    f"Image URL redirected (to {resp.headers.get('Location')}). "
                    "For safety, redirects are not followed. Use the direct URL."
                )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Failed to fetch image from {value}: {e}")

        if len(resp.content) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"Image too large ({len(resp.content)} bytes). "
                f"Maximum allowed: {MAX_IMAGE_BYTES} bytes (20MB)."
            )
        content_type = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
        data, mime = _try_fit(resp.content, content_type)
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    if parsed.scheme == "file":
        path = parsed.path
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise ValueError(f"Failed to read image file {path}: {e}")

        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"Image too large ({len(data)} bytes). "
                f"Maximum allowed: {MAX_IMAGE_BYTES} bytes (20MB)."
            )

        mime, _ = mimetypes.guess_type(path)
        if mime is None:
            mime = "image/png"
        data, mime = _try_fit(data, mime)
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    raise ValueError(
        f"Unsupported image value: {value!r}. "
        "Expected a data URI (data:image/...), HTTP(S) URL, or file:// URI."
    )
