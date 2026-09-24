"""ASGI entrypoint for production deployment."""

from runtime_settings import get_setting

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host=get_setting("OMR_HOST", "0.0.0.0"),
        port=int(get_setting("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
