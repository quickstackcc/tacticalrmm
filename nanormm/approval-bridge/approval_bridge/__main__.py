import uvicorn

from .app import create_app
from .settings import BridgeSettings


def main() -> None:
    settings = BridgeSettings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
