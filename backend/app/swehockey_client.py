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
import unicodedata
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://stats.swehockey.se"
TSDB_API  = "https://www.thesportsdb.com/api/v1/json/3"

# Default group ID for SHL 2025-26 (updated each season via env var)
DEFAULT_GROUP_ID = os.getenv("SWE_GROUP_ID", "18263")

# Hints for teams whose swehockey.se name doesn't map cleanly to a short code.
# Only needed for irregularities (e.g. spaces, abbreviations).
# Teams not listed here fall back to the first 4 chars of their name uppercased.
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

    async def download_team_logos(
        self,
        team_names: dict[str, str],
        logos_dir: str,
    ) -> dict[str, str]:
        """
        Ensure each team has a logo file on disk inside *logos_dir*.

        - Skips teams whose file already exists.
        - Downloads missing logos from TheSportsDB and saves them.
        - Returns {team_code: local_filename} for every team that has a file.
        """
        import asyncio as _asyncio
        import os

        os.makedirs(logos_dir, exist_ok=True)
        local: dict[str, str] = {}

        # Find which teams already have a file
        existing = {
            fname.split(".")[0]: fname
            for fname in os.listdir(logos_dir)
            if "." in fname
        }
        for code in team_names:
            if code in existing:
                local[code] = existing[code]

        missing = {c: n for c, n in team_names.items() if c not in local}
        if not missing:
            return local

        logger.info("Downloading logos for: %s", list(missing.keys()))

        async with httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SHLLiveStandings/1.0)"},
            follow_redirects=True,
        ) as client:
            for code, name in missing.items():
                badge_url = await _tsdb_badge_url(client, name)
                if badge_url:
                    ext = badge_url.rsplit(".", 1)[-1].split("?")[0] or "png"
                    filename = f"{code}.{ext}"
                    filepath = os.path.join(logos_dir, filename)
                    try:
                        img_resp = await client.get(badge_url)
                        img_resp.raise_for_status()
                        with open(filepath, "wb") as fh:
                            fh.write(img_resp.content)
                        local[code] = filename
                        logger.info("Logo saved: %s", filename)
                    except Exception as exc:
                        logger.warning("Failed to download logo for %s: %s", code, exc)
                else:
                    logger.warning("TSDB: no badge found for %s (%r)", code, name)
                # Respect TSDB free-tier rate limit
                await _asyncio.sleep(0.6)

        return local


# ------------------------------------------------------------------
# TheSportsDB logo lookup
# ------------------------------------------------------------------

def _ascii_name(s: str) -> str:
    """Strip diacritics so Swedish names work in TSDB search."""
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()


async def _tsdb_badge_url(client: httpx.AsyncClient, full_name: str) -> str | None:
    """
    Search TheSportsDB for a team by name and return its badge URL.
    Tries the full name first, then the first word only as a fallback.
    Filters results to Swedish teams to avoid false matches.
    Retries once with backoff on HTTP 429.
    """
    import asyncio as _asyncio

    for query in (_ascii_name(full_name), _ascii_name(full_name.split()[0])):
        for attempt in range(2):  # one retry on 429
            try:
                resp = await client.get(
                    f"{TSDB_API}/searchteams.php",
                    params={"t": query},
                )
                if resp.status_code == 429:
                    wait = 2.0 * (attempt + 1)
                    logger.warning("TSDB 429 for %r, retrying in %.1fs", query, wait)
                    await _asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                teams = (resp.json() or {}).get("teams") or []
                # Prefer teams in Sweden / SHL; fall back to first result
                ranked = sorted(
                    teams,
                    key=lambda t: (
                        "sweden" not in (t.get("strCountry") or "").lower(),
                        "shl" not in (t.get("strLeague") or "").lower(),
                    ),
                )
                for t in ranked:
                    badge = t.get("strTeamBadge") or t.get("strTeamBadge2") or ""
                    if badge:
                        return badge
                break  # got a valid (possibly empty) response – no retry needed
            except Exception as exc:
                logger.warning("TSDB search failed for %r: %s", query, exc)
                break
    return None


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
            gp   = int(cells[2])
            w    = int(cells[3])
            l    = int(cells[5])
            gd   = int(cells[7])
            pts  = int(cells[8])
            # OTW + GWSW = all OT/PS wins (2 pts each)
            # OTL + GWSL = all OT/PS losses (1 pt each)
            otw  = int(cells[9])  + int(cells[11]) if len(cells) > 11 else (int(cells[9])  if len(cells) > 9  else 0)
            otl  = int(cells[10]) + int(cells[12]) if len(cells) > 12 else (int(cells[10]) if len(cells) > 10 else 0)
        except ValueError:
            continue

        rows.append(
            {
                "team": {"code": code, "name": name},
                "Points": pts,
                "GP": gp,
                "W": w,
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

    Live scores are in div.TodaysGamesGame blocks (NOT tables):
      - Score div (has class "p-1"): home team, score link, away team
      - Status div (has class "pt-0"): period status text
    """
    soup = BeautifulSoup(html, "html.parser")
    games: list[dict] = []

    score_re = re.compile(r"(\d+)\s*-\s*(\d+)")

    game_divs = soup.find_all("div", class_="TodaysGamesGame")

    i = 0
    while i < len(game_divs):
        div = game_divs[i]
        classes = div.get("class", [])

        # Score divs have "p-1"; status divs have "pt-0" – skip status divs here
        if "p-1" not in classes:
            i += 1
            continue

        # Home team is in col-5 text-right, away in col-5 text-left
        home_div = div.find("div", class_=lambda c: c and "text-right" in c)
        away_div = div.find("div", class_=lambda c: c and "text-left" in c)
        result_div = div.find("div", class_=lambda c: c and "Result" in c)

        if not (home_div and away_div and result_div):
            i += 1
            continue

        home_name = home_div.get_text(strip=True)
        away_name = away_div.get_text(strip=True)

        # Score is in the <a> inside Result div (e.g. "2 - 1")
        score_link = result_div.find("a")
        score_text = score_link.get_text(strip=True) if score_link else ""
        score_match = score_re.search(score_text)
        if not score_match:
            i += 1
            continue

        home_score = int(score_match.group(1))
        away_score = int(score_match.group(2))

        # Status text is in the immediately following TodaysGamesGame div (pt-0)
        status_text = ""
        if i + 1 < len(game_divs):
            next_div = game_divs[i + 1]
            if "pt-0" in next_div.get("class", []):
                status_text = next_div.get_text(strip=True)

        # Determine period from status
        status_lower = status_text.lower()
        if "3rd period" in status_lower or "p3" in status_lower:
            period = 3
        elif "2nd period" in status_lower or "p2" in status_lower:
            period = 2
        elif "ot" in status_lower or "overtime" in status_lower:
            period = 4
        else:
            period = 1  # 1st period or unknown

        # Mark as played (finished) only if status indicates final
        is_finished = any(
            word in status_lower for word in ("final", "slut", "game over")
        )

        games.append(
            {
                "homeTeam": {"code": _team_code(home_name), "name": home_name},
                "awayTeam": {"code": _team_code(away_name), "name": away_name},
                "played": is_finished,
                "liveGame": {
                    "homeTeamScore": home_score,
                    "awayTeamScore": away_score,
                    "period": period,
                    "statusString": status_text,
                },
            }
        )

        i += 1

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
            # The row may also contain game data (date in col 0, time in col 2,
            # teams in col 3) — fall through instead of skipping it.
            if len(cells) < 4:
                continue
        if current_date is None or len(cells) < 4:
            continue

        game_text = cells[3]
        parts = team_sep_re.split(game_text)
        if len(parts) < 2:
            continue

        home_team = " ".join(parts[0].split())
        away_team = " ".join(parts[1].split())
        # When cells[0] is a date, the time is in cells[2]; otherwise cells[0].
        time_str  = cells[2].strip() if date_re.match(c0) else cells[0].strip()
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
