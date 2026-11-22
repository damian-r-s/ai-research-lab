"""Local development server with auto-reload."""

import uvicorn

from src.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    # proxy_headers=False: client addresses are resolved by the API's trusted-proxy rules only.
    uvicorn.run(
        "app:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=True,
        proxy_headers=False,
    )
