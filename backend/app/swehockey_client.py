"""
Fallback data client that scrapes stats.swehockey.se.

Used when SHL Open API credentials (SHL_CLIENT_ID / SHL_CLIENT_SECRET) are
not configured.  Returns data in the same shape as SHLClient so the rest of
the application is unaffected.

Standings URL:  https://stats.swehockey.se/ScheduleAndResults/Standings/{gid}
Live URL:       https://stats.swehockey.se/ScheduleAndResults/Live/{gid}
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://stats.swehockey.se"

# Default group ID for SHL 2025-26 (updated each season via env var)
DEFAULT_GROUP_ID = os.getenv("SWE_GROUP_ID", "18263")

# Canonical team-code lookup for current SHL clubs.
# Keys are substrings that appear in the team name on swehockey.se.
TEAM_CODE_MAP: dict[str, str] = {
    "Brynäs": "BRY",
    "Djurgården": "DIF",
    "Frölunda": "FHC",
    "HV71": "HV71",
    "Leksands": "LIF",
    "Linköping": "LHC",
    "Luleå": "LHF",
    "Malmö": "MR",
    "Modo": "MOD",
    "Örebro": "OHK",
    "Rögle": "RBK",
    "Skellefteå": "SAIK",
    "Timrå": "TIK",
    "Växjö": "VLH",
}


def _team_code(name: str) -> str:
    """Map a full team name to a short code."""
    for key, code in TEAM_CODE_MAP.items():
        if key.lower() in name.lower():
            return code
    # Fallback: uppercase first 4 chars
    words = name.split()
    return (words[0][:4]).upper() if words else "UNK"


def _parse_gf_ga(cell: str) -> tuple[int, int]:
    """Parse '179:119' → (179, 119)."""
    m = re.match(r"(\d+)\s*:\s*(\d+)", cell.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


class SweHockeyClient:
    """Async client that scrapes stats.swehockey.se for SHL data."""

    def __init__(self, group_id: Optional[str] = None):
        self.group_id = group_id or DEFAULT_GROUP_ID
        self.season = os.getenv("SHL_SEASON", "2025")

    async def _get(self, path: str) -> str:
        url = f"{BASE_URL}{path}"
        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SHLLiveStandings/1.0)"},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    # ------------------------------------------------------------------
    # Public interface – mirrors SHLClient
    # ------------------------------------------------------------------

    async def get_standings(self) -> list[dict]:
        """Return standings in SHL-API-compatible format."""
        html = await self._get(f"/ScheduleAndResults/Standings/{self.group_id}")
        return _parse_standings(html)

    async def get_games(self) -> list[dict]:
        """Return today's live games in SHL-API-compatible format."""
        html = await self._get(f"/ScheduleAndResults/Live/{self.group_id}")
        return _parse_live_games(html)


# ------------------------------------------------------------------
# HTML parsers
# ------------------------------------------------------------------

def _parse_standings(html: str) -> list[dict]:
    """
    Parse the first standings table from the Standings page.

    Expected columns (may vary by season):
        RK, Team, GP, W, T, L, GF:GA, GD, TP, OTW, OTL, GWSW, GWSL
    """
    soup = BeautifulSoup(html, "html.parser")

    # Find the first table that looks like a standings table
    table = None
    for t in soup.find_all("table"):
        headers = [th.get_text(strip=True).upper() for th in t.find_all("th")]
        if any(h in ("RK", "TP", "GP") for h in headers):
            table = t
            break

    if table is None:
        logger.warning("SweHockey: standings table not found")
        return []

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    # Normalize header names
    col = {h.upper(): i for i, h in enumerate(headers)}

    rows = []
    for tr in table.find_all("tr")[1:]:  # skip header row
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue

        def _get(key: str, default="0") -> str:
            idx = col.get(key)
            if idx is None or idx >= len(cells):
                return default
            return cells[idx] or default

        name = _get("TEAM", cells[1] if len(cells) > 1 else "Unknown")
        code = _team_code(name)
        gf, ga = _parse_gf_ga(_get("GF:GA", "0:0"))

        try:
            gp = int(_get("GP"))
            wins = int(_get("W", "0"))
            ties = int(_get("T", "0"))
            losses = int(_get("L", "0"))
            pts = int(_get("TP", "0"))
            otw = int(_get("OTW", "0"))
            otl = int(_get("OTL", "0"))
        except ValueError:
            continue

        rows.append(
            {
                "team": {"code": code, "name": name},
                "Points": pts,
                "GP": gp,
                "W": wins,
                "T": ties,
                "L": losses,
                "OTW": otw,
                "OTL": otl,
                "GF": gf,
                "GA": ga,
                "Diff": gf - ga,
            }
        )

    return rows


def _parse_live_games(html: str) -> list[dict]:
    """
    Parse the Live page for ongoing games.

    The live page shows today's games with their current score.
    Returns games in SHL-API-compatible format (liveGame block included).
    """
    soup = BeautifulSoup(html, "html.parser")
    games: list[dict] = []

    # Look for game rows – the live page typically uses a table or
    # repeated div blocks with home/away teams and scores.
    table = None
    for t in soup.find_all("table"):
        headers = [th.get_text(strip=True).upper() for th in t.find_all("th")]
        # Live table usually has no standard headers but contains score-like cells
        rows = t.find_all("tr")
        if len(rows) > 1:
            table = t
            break

    if table is None:
        return []

    score_re = re.compile(r"(\d+)\s*[-–]\s*(\d+)")
    period_re = re.compile(r"\b([1-4])\b")

    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue

        # Try to find a score in any cell
        score_cell = None
        score_match = None
        for cell in cells:
            m = score_re.search(cell)
            if m:
                score_cell = cell
                score_match = m
                break

        if not score_match:
            continue

        home_score = int(score_match.group(1))
        away_score = int(score_match.group(2))

        # Derive team names: assume cell[0] = home, cell[-1] or nearby = away
        # The game column typically reads "HomeTeam - AwayTeam" or similar
        game_text = cells[0] if cells else ""
        teams = re.split(r"\s+-\s+", game_text, maxsplit=1)
        home_name = teams[0].strip() if len(teams) >= 1 else "?"
        away_name = teams[1].strip() if len(teams) >= 2 else "?"

        # Try to find period info
        period = 0
        status_text = score_cell or ""
        pm = period_re.search(status_text)
        if pm:
            period = int(pm.group(1))
        elif "OT" in status_text.upper():
            period = 4

        # Only include if the game appears to be ongoing (period > 0)
        if period == 0:
            period = 1  # assume at least started

        games.append(
            {
                "homeTeam": {"code": _team_code(home_name), "name": home_name},
                "awayTeam": {"code": _team_code(away_name), "name": away_name},
                "played": False,
                "liveGame": {
                    "homeTeamScore": home_score,
                    "awayTeamScore": away_score,
                    "period": period,
                    "statusString": status_text,
                },
            }
        )

    return games
