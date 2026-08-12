"""Точка входа FastAPI для Пацанский Ход (Mini App).

Слушает 127.0.0.1:8788. Reverse proxy: Caddy на gopgame.duckdns.org.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from webapp.routes import router, configure as configure_routes

# Загружаем .env из корня gop-bot
# webapp_main.py лежит в /srv/openclaw-bus/gop-bot/, не в webapp/
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE  # gop-bot root = . parent
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gop-bot.webapp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # WorkingDirectory юнита = /srv/openclaw-bus/gop-bot, но Path(__file__).parent.parent
    # указывает в /srv/openclaw-bus. Используем явный путь.
    db_path = Path("/srv/openclaw-bus/gop-bot/data/gop.db")
    bot_token = os.environ.get("BOT_TOKEN", "")
    if not bot_token:
        log.warning("BOT_TOKEN not set — WebApp auth will fail")
    configure_routes(db_path, bot_token)
    log.info("WebApp ready: db=%s", db_path)
    yield


app = FastAPI(title="Пацанский Ход", lifespan=lifespan)
app.include_router(router)

# Static — в webapp/static/
_static = _HERE / "webapp" / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

# Templates — Jinja2
_templates_dir = _HERE / "webapp" / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


# HTML
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    return templates.TemplateResponse(request, "profile.html")


@app.get("/actions", response_class=HTMLResponse)
def actions(request: Request):
    return templates.TemplateResponse(request, "actions.html")


@app.get("/healthz")
def healthz_alias():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "webapp_main:app",
        host="127.0.0.1",
        port=8788,
        log_level="info",
        access_log=True,
    )
