"""Canned platform responses and images for the tests."""

import io

import httpx
from PIL import Image


def png(size: tuple[int, int] = (1200, 627), color=(200, 210, 220)) -> bytes:
    """A real, decodable PNG, as /image returns one (unbranded)."""
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def size_of(image: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(image)).size


def response(status: int, json: dict | None = None, headers: dict | None = None,
             text: str | None = None) -> httpx.Response:
    """A canned platform response."""
    request = httpx.Request("POST", "https://platform.test")
    if json is not None:
        return httpx.Response(status, json=json, headers=headers, request=request)
    return httpx.Response(status, text=text or "", headers=headers, request=request)
