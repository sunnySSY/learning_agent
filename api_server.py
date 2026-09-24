"""Run the Phase 5 API with Uvicorn."""

import uvicorn

from config import cfg


if __name__ == "__main__":
    uvicorn.run("app.api:app", host=cfg.api_host, port=cfg.api_port, reload=False)
