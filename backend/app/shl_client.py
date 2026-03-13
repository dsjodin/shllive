import os
import time
import logging
from base64 import b64encode
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.shl.se"


class SHLClient:
    def __init__(self):
        self.client_id = os.getenv("SHL_CLIENT_ID", "")
        self.client_secret = os.getenv("SHL_CLIENT_SECRET", "")
        self.season = os.getenv("SHL_SEASON", "2025")
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    async def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise ValueError("SHL_CLIENT_ID and SHL_CLIENT_SECRET must be set")

        credentials = b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BASE_URL}/oauth2/token",
                headers={"Authorization": f"Basic {credentials}"},
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
        logger.info("SHL token refreshed")
        return self._access_token

    async def get_standings(self) -> list[dict]:
        token = await self._get_token()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BASE_URL}/seasons/{self.season}/statistics/teams/standings",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_games(self) -> list[dict]:
        token = await self._get_token()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{BASE_URL}/seasons/{self.season}/games",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "include": (
                        "liveGame.homeTeamScore,"
                        "liveGame.awayTeamScore,"
                        "liveGame.period,"
                        "liveGame.periodTime,"
                        "liveGame.statusString,"
                        "homeTeam.code,"
                        "homeTeam.name,"
                        "awayTeam.code,"
                        "awayTeam.name"
                    )
                },
            )
            resp.raise_for_status()
            return resp.json()
