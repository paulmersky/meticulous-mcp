"""Tests for image_utils module."""

import base64
import io
from unittest.mock import Mock, patch

import pytest

from meticulous_mcp.image_utils import resolve_image


def test_data_uri_passthrough():
    """Data URIs are returned as-is."""
    uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    assert resolve_image(uri) == uri


def test_http_url_fetch():
    """HTTP URLs are fetched, base64-encoded, and returned as data URIs."""
    raw_bytes = b"\x89PNG\r\n\x1a\n"
    expected_b64 = base64.b64encode(raw_bytes).decode("ascii")

    mock_resp = Mock()
    mock_resp.content = raw_bytes
    mock_resp.headers = {"Content-Type": "image/png"}
    mock_resp.raise_for_status = Mock()
    mock_resp.is_redirect = False
    mock_resp.is_permanent_redirect = False

    with patch("meticulous_mcp.image_utils.requests.get", return_value=mock_resp) as mock_get:
        result = resolve_image("http://example.com/img.png")
        mock_get.assert_called_once_with(
            "http://example.com/img.png", timeout=30, allow_redirects=False
        )

    assert result == f"data:image/png;base64,{expected_b64}"


def test_https_url_fetch():
    """HTTPS URLs work the same as HTTP."""
    raw_bytes = b"\xff\xd8\xff\xe0"
    expected_b64 = base64.b64encode(raw_bytes).decode("ascii")

    mock_resp = Mock()
    mock_resp.content = raw_bytes
    mock_resp.headers = {"Content-Type": "image/jpeg; charset=utf-8"}
    mock_resp.raise_for_status = Mock()
    mock_resp.is_redirect = False
    mock_resp.is_permanent_redirect = False

    with patch("meticulous_mcp.image_utils.requests.get", return_value=mock_resp):
        result = resolve_image("https://example.com/photo.jpg")

    assert result == f"data:image/jpeg;base64,{expected_b64}"


def test_file_uri_read(tmp_path):
    """file:// URIs read from disk and encode."""
    content = b"fake png data"
    expected_b64 = base64.b64encode(content).decode("ascii")

    path = tmp_path / "test.png"
    path.write_bytes(content)

    result = resolve_image(f"file://{path}")
    assert result == f"data:image/png;base64,{expected_b64}"


def test_file_uri_nonexistent():
    """file:// URI for missing file raises ValueError."""
    with pytest.raises(ValueError, match="Failed to read image file"):
        resolve_image("file:///nonexistent/path/image.png")


def test_http_error():
    """HTTP errors raise ValueError."""
    import requests as req

    mock_resp = Mock()
    mock_resp.is_redirect = False
    mock_resp.is_permanent_redirect = False
    mock_resp.raise_for_status.side_effect = req.HTTPError("404 Not Found")

    with patch("meticulous_mcp.image_utils.requests.get", return_value=mock_resp):
        with pytest.raises(ValueError, match="Failed to fetch image"):
            resolve_image("http://example.com/missing.png")


def test_invalid_scheme():
    """Unsupported schemes raise ValueError."""
    with pytest.raises(ValueError, match="Unsupported image value"):
        resolve_image("ftp://example.com/image.png")


def test_oversized_image_auto_resized():
    """Images exceeding the size limit are automatically resized."""
    import random
    from PIL import Image
    from meticulous_mcp.image_utils import MAX_B64_BYTES

    # Create a large noisy image that won't compress well
    random.seed(42)
    img = Image.new("RGB", (4096, 4096))
    pixels = img.load()
    for y in range(0, 4096, 4):
        for x in range(0, 4096, 4):
            c = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            for dy in range(4):
                for dx in range(4):
                    pixels[x + dx, y + dy] = c
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    large_png = buf.getvalue()

    # Confirm it's actually over the limit
    assert len(base64.b64encode(large_png)) > MAX_B64_BYTES

    mock_resp = Mock()
    mock_resp.content = large_png
    mock_resp.headers = {"Content-Type": "image/png"}
    mock_resp.raise_for_status = Mock()
    mock_resp.is_redirect = False
    mock_resp.is_permanent_redirect = False

    with patch("meticulous_mcp.image_utils.requests.get", return_value=mock_resp):
        result = resolve_image("http://example.com/huge.png")

    # Result should be a valid data URI
    assert result.startswith("data:image/jpeg;base64,")
    # And the base64 payload should be under the limit
    b64_part = result.split(",", 1)[1]
    assert len(b64_part) <= MAX_B64_BYTES


def test_small_image_not_resized(tmp_path):
    """Images under the size limit are not modified."""
    from PIL import Image

    # Create a small image
    img = Image.new("RGB", (64, 64), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    small_png = buf.getvalue()
    expected_b64 = base64.b64encode(small_png).decode("ascii")

    path = tmp_path / "small.png"
    path.write_bytes(small_png)

    result = resolve_image(f"file://{path}")
    # Should keep original PNG format since it's small enough
    assert result == f"data:image/png;base64,{expected_b64}"
