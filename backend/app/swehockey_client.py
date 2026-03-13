"""
Data client that scrapes stats.swehockey.se.

Standings URL:  https://stats.swehockey.se/ScheduleAndResults/Standings/{gid}
Live URL:       https://stats.swehockey.se/ScheduleAndResults/Live/{gid}
Schedule URL:   https://stats.swehockey.se/ScheduleAndResults/Schedule/{gid}
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
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
    "Djurgård": "DIF",
    "Färjestad": "FBK",
    "Frölunda": "FHC",
    "HV 71": "HV71",
    "HV71": "HV71",
    "Leksands": "LIF",
    "Linköping": "LHC",
    "Luleå": "LHF",
    "Malmö": "MIF",
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

    async def get_schedule(self) -> dict:
        """Return today's and next upcoming round of games."""
        html = await self._get(f"/ScheduleAndResults/Schedule/{self.group_id}")
        return _parse_schedule(html)


# ------------------------------------------------------------------
# HTML parsers
# ------------------------------------------------------------------

def _parse_standings(html: str) -> list[dict]:
    """
    Parse the standings table from stats.swehockey.se.

    The first table with class 'tblContent' holds the full standings.
    Column order (positional):
        0=RK, 1=TeamName, 2=GP, 3=W, 4=T, 5=L,
        6=GF:GA(GD), 7=GD, 8=TP, 9=OTW, 10=OTL, 11=GWSW, 12=GWSL
    """
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table", class_="tblContent")
    if table is None:
        logger.warning("SweHockey: standings table not found")
        return []

    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all("td")]
        # Need at least: RK, Name, GP, W, T, L, GF:GA, GD, TP
        if len(cells) < 9:
            continue
        if not cells[0].isdigit():
            continue

        name = cells[1]
        code = _team_code(name)
        gf, ga = _parse_gf_ga(cells[6])

        try:
            gp  = int(cells[2])
            w   = int(cells[3])
            t   = int(cells[4])
            l   = int(cells[5])
            gd  = int(cells[7])
            pts = int(cells[8])
            otw = int(cells[9])  if len(cells) > 9  else 0
            otl = int(cells[10]) if len(cells) > 10 else 0
        except ValueError:
            continue

        rows.append(
            {
                "team": {"code": code, "name": name},
                "Points": pts,
                "GP": gp,
                "W": w,
                "T": t,
                "L": l,
                "OTW": otw,
                "OTL": otl,
                "GF": gf,
                "GA": ga,
                "Diff": gd,
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


def _parse_schedule(html: str) -> dict:
    """
    Parse the schedule page and return today's games and the next upcoming round.

    Returns:
        {
            "round_date": "YYYY-MM-DD",
            "games": [
                {
                    "time": "HH:MM",
                    "home_team": str,
                    "home_code": str,
                    "away_team": str,
                    "away_code": str,
                    "result": str | None,   # e.g. "3-2", None if not played
                    "played": bool,
                }
            ]
        }
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="tblContent")
    if table is None:
        return {"round_date": None, "games": []}

    today = str(date.today())
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    # Non-breaking-space dash used as separator between team names
    team_sep_re = re.compile(r"\s*\xa0-\xa0\s*")
    result_re = re.compile(r"(\d+)\s*\xa0-\xa0\s*(\d+)")

    # Collect all rounds (date → list of game dicts)
    rounds: dict[str, list[dict]] = {}
    current_date: str | None = None

    for tr in table.find_all("tr"):
        cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        c0 = cells[0].strip()
        if date_re.match(c0):
            current_date = c0
            continue
        if current_date is None or len(cells) < 4:
            continue

        game_text = cells[3]
        parts = team_sep_re.split(game_text)
        if len(parts) < 2:
            continue

        home_team = " ".join(parts[0].split())
        away_team = " ".join(parts[1].split())
        time_str  = cells[0].strip()
        result_raw = cells[4].strip() if len(cells) > 4 else ""
        rm = result_re.search(result_raw)
        result    = f"{rm.group(1)}-{rm.group(2)}" if rm else None
        played    = rm is not None

        rounds.setdefault(current_date, []).append({
            "time":      time_str,
            "home_team": home_team,
            "home_code": _team_code(home_team),
            "away_team": away_team,
            "away_code": _team_code(away_team),
            "result":    result,
            "played":    played,
        })

    # Find the best round to show:
    # 1. Today's games if any exist (live or already finished today)
    # 2. Otherwise the first upcoming date with unplayed games
    if today in rounds:
        return {"round_date": today, "games": rounds[today]}

    for d in sorted(rounds):
        if d > today and any(not g["played"] for g in rounds[d]):
            return {"round_date": d, "games": rounds[d]}

    return {"round_date": None, "games": []}
