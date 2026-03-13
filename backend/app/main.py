import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .swehockey_client import SweHockeyClient
from .standings import calculate_live_standings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data client – stats.swehockey.se scraper
# ---------------------------------------------------------------------------
shl = SweHockeyClient()

# ---------------------------------------------------------------------------
# Simple in-memory cache
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {
    "standings": None,
    "standings_ts": 0.0,
    "games": None,
    "games_ts": 0.0,
    "schedule": None,
    "schedule_ts": 0.0,
    "logos": None,
    "logos_ts": 0.0,
}
STANDINGS_TTL = 300      # 5 minutes
GAMES_TTL     = 30       # 30 seconds
SCHEDULE_TTL  = 600      # 10 minutes
LOGOS_TTL     = 86_400   # 24 hours


async def _fetch_standings() -> list[dict]:
    now = time.time()
    if _cache["standings"] is not None and now - _cache["standings_ts"] < STANDINGS_TTL:
        return _cache["standings"]
    try:
        data = await shl.get_standings()
        _cache["standings"] = data
        _cache["standings_ts"] = now
        return data
    except Exception as exc:
        logger.error("Failed to fetch standings: %s", exc)
        if _cache["standings"] is not None:
            return _cache["standings"]
        raise


async def _fetch_games() -> list[dict]:
    now = time.time()
    if _cache["games"] is not None and now - _cache["games_ts"] < GAMES_TTL:
        return _cache["games"]
    try:
        data = await shl.get_games()
        _cache["games"] = data
        _cache["games_ts"] = now
        return data
    except Exception as exc:
        logger.error("Failed to fetch games: %s", exc)
        if _cache["games"] is not None:
            return _cache["games"]
        raise


async def _fetch_schedule() -> dict:
    now = time.time()
    if _cache["schedule"] is not None and now - _cache["schedule_ts"] < SCHEDULE_TTL:
        return _cache["schedule"]
    try:
        data = await shl.get_schedule()
        _cache["schedule"] = data
        _cache["schedule_ts"] = now
        return data
    except Exception as exc:
        logger.error("Failed to fetch schedule: %s", exc)
        if _cache["schedule"] is not None:
            return _cache["schedule"]
        return {"round_date": None, "games": []}


async def _fetch_logos() -> dict:
    now = time.time()
    if _cache["logos"] is not None and now - _cache["logos_ts"] < LOGOS_TTL:
        return _cache["logos"]
    try:
        standings = await _fetch_standings()
        team_names: dict[str, str] = {
            e["team"]["code"]: e["team"]["name"]
            for e in standings
            if isinstance(e.get("team"), dict) and e["team"].get("code")
        }
        data = await shl.get_team_logos(team_names)
        if data:  # only store if we got results
            _cache["logos"] = data
            _cache["logos_ts"] = now
        return _cache["logos"] or {}
    except Exception as exc:
        logger.error("Failed to fetch team logos: %s", exc)
        return _cache["logos"] or {}


async def _build_payload() -> dict:
    standings = await _fetch_standings()
    games = await _fetch_games()
    schedule = await _fetch_schedule()
    logos = await _fetch_logos()
    live_table, live_games = calculate_live_standings(standings, games)
    has_live = len(live_games) > 0
    return {
        "standings": live_table,
        "live_games": live_games,
        "has_live": has_live,
        "base_standings": standings,
        "schedule": schedule,
        "logos": logos,
        "season": shl.season,
        "updated_at": time.time(),
    }


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up caches on startup (best-effort, logos fetched separately as they're slow)
    try:
        await _build_payload()
        logger.info("Cache warmed up successfully")
    except Exception as exc:
        logger.warning("Startup cache warm-up failed: %s", exc)
    # Fetch logos in background so startup isn't blocked
    asyncio.create_task(_fetch_logos())
    yield


app = FastAPI(title="SHL Live Standings", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/team-logos")
async def get_team_logos_endpoint():
    """Team badge URLs fetched from TheSportsDB."""
    return await _fetch_logos()


@app.get("/api/standings")
async def get_standings():
    """Raw standings from SHL API (no live adjustments)."""
    data = await _fetch_standings()
    return {"standings": data, "season": shl.season}


@app.get("/api/live-standings")
async def get_live_standings():
    """Standings recalculated with projected live game results."""
    return await _build_payload()


# ---------------------------------------------------------------------------
# WebSocket – pushes updates every 30 s
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self._connections.remove(ws)

    async def broadcast(self, data: dict):
        for ws in list(self._connections):
            try:
                await ws.send_json(data)
            except Exception:
                self._connections.remove(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Send immediately on connect
        payload = await _build_payload()
        await ws.send_json(payload)

        while True:
            await asyncio.sleep(30)
            payload = await _build_payload()
            await ws.send_json(payload)
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        manager.disconnect(ws)
