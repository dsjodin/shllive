"""
Live standings calculation.

The base standings from the SHL API reflect completed games only.
For each ongoing live game we project the final result based on
the current score and add the corresponding points on top.

Points system (SHL):
  Win (regulation)  → 3 pts for winner, 0 for loser
  Win (OT/SO)       → 2 pts for winner, 1 for loser
  Tied live game    → both teams earn at least 1 pt (OT guaranteed);
                       we split the remaining point 0.5/0.5 visually
                       but since points are integers we mark them as
                       "pending" in the live game metadata.
"""

from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone


def _is_live(game: dict) -> bool:
    live = game.get("live_game") or game.get("liveGame")
    if not live:
        return False
    # Games that have a live_game block with non-zero scores or an active status
    status = live.get("statusString") or live.get("status_string") or ""
    played = game.get("played", False)
    if played:
        return False
    # If the game has started (period > 0) but not finished
    period = live.get("period", 0)
    return period is not None and period >= 1


def _team_code(game: dict, side: str) -> str:
    """Extract team code for 'home' or 'away' side."""
    # Try nested object first, then flat code field
    team_obj = game.get(f"{side}_team") or game.get(f"{side}Team") or {}
    if isinstance(team_obj, dict):
        code = team_obj.get("code") or team_obj.get("teamCode") or ""
        if code:
            return code
    return game.get(f"{side}_team_code") or game.get(f"{side}TeamCode") or ""


def _team_name(game: dict, side: str) -> str:
    team_obj = game.get(f"{side}_team") or game.get(f"{side}Team") or {}
    if isinstance(team_obj, dict):
        name = team_obj.get("name") or ""
        if name:
            return name
    return _team_code(game, side)


def _live_scores(game: dict) -> tuple[int, int]:
    live = game.get("live_game") or game.get("liveGame") or {}
    home = (
        live.get("homeTeamScore")
        or live.get("home_team_score")
        or game.get("home_team_result")
        or 0
    )
    away = (
        live.get("awayTeamScore")
        or live.get("away_team_score")
        or game.get("away_team_result")
        or 0
    )
    return int(home), int(away)


def _live_status(game: dict) -> str:
    live = game.get("live_game") or game.get("liveGame") or {}
    return live.get("statusString") or live.get("status_string") or "Pågår"


def calculate_live_standings(
    standings: list[dict],
    games: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Returns:
        (live_table, live_games_info)

        live_table       – same structure as API standings, re-sorted
        live_games_info  – list of dicts describing ongoing games
    """
    # Build mutable index keyed by team code
    table: dict[str, dict] = {}
    for entry in standings:
        team = entry.get("team") or {}
        code = team.get("code") or team.get("teamCode") or entry.get("teamCode") or ""
        if code:
            table[code] = deepcopy(entry)

    live_games_info: list[dict] = []

    for game in games:
        if not _is_live(game):
            continue

        home_code = _team_code(game, "home")
        away_code = _team_code(game, "away")
        home_score, away_score = _live_scores(game)
        status = _live_status(game)

        home_pts = 0
        away_pts = 0
        result_label = ""

        if home_score > away_score:
            home_pts, away_pts = 3, 0
            result_label = f"{_team_name(game, 'home')} leder"
        elif away_score > home_score:
            home_pts, away_pts = 0, 3
            result_label = f"{_team_name(game, 'away')} leder"
        else:
            # Tied → OT guaranteed → minimum 1 pt each; show as 1+? / 1+?
            home_pts, away_pts = 1, 1
            result_label = "Lika – förlängning avgör"

        if home_code in table:
            table[home_code]["Points"] = table[home_code].get("Points", 0) + home_pts
            table[home_code]["_live_pts"] = home_pts
            table[home_code]["GF"] = table[home_code].get("GF", 0) + home_score
            table[home_code]["GA"] = table[home_code].get("GA", 0) + away_score
            table[home_code]["Diff"] = table[home_code]["GF"] - table[home_code]["GA"]
        if away_code in table:
            table[away_code]["Points"] = table[away_code].get("Points", 0) + away_pts
            table[away_code]["_live_pts"] = away_pts
            table[away_code]["GF"] = table[away_code].get("GF", 0) + away_score
            table[away_code]["GA"] = table[away_code].get("GA", 0) + home_score
            table[away_code]["Diff"] = table[away_code]["GF"] - table[away_code]["GA"]

        live_games_info.append(
            {
                "home_team_code": home_code,
                "home_team_name": _team_name(game, "home"),
                "away_team_code": away_code,
                "away_team_name": _team_name(game, "away"),
                "home_score": home_score,
                "away_score": away_score,
                "status": status,
                "result_label": result_label,
                "home_projected_pts": home_pts,
                "away_projected_pts": away_pts,
            }
        )

    # Re-sort: Points desc → GF-GA (Diff) desc → GF desc
    def sort_key(e: dict):
        diff = e.get("Diff") or (e.get("GF", 0) - e.get("GA", 0))
        return (-e.get("Points", 0), -diff, -e.get("GF", 0))

    sorted_table = sorted(table.values(), key=sort_key)
    for i, entry in enumerate(sorted_table, 1):
        entry["rank"] = i

    return sorted_table, live_games_info
