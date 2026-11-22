"""ASGI entry point: `uvicorn app:app` (same convention as the sibling project)."""

from src.api.app import create_app

app = create_app()
