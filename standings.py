"""The 7 Stars league table, computed from the FPL API.

Each manager holds one GK club and one DEF club all season, plus seven
goal scorers that a transfer window can change. Every gameweek their
scorers' goals (GS) are played off against the goals their two clubs
conceded (GA) -- managers are not drawn against each other, which is why
the league's total GS and total GA do not match.

  GS > GA  win (3)      GS == GA  draw (1)      GS < GA  loss (0)

All of this was reverse-engineered from the league's own spreadsheet and
checked against the API: see test_standings.py, which pins the facts that
confirmed it.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import fpl

TEAMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teams.json")

# The Own Goals pick, which scores every own goal in the league that week.
OWN_GOALS = "OWN"


def load_league(path=TEAMS_PATH):
    return json.load(open(path))


def conceded_by_club(ttl=fpl.CACHE_TTL):
    """{club name: {gameweek: goals conceded}} straight off the scorelines."""
    teams = {t["id"]: t["name"] for t in fpl.bootstrap(ttl)["teams"]}
    out = {}
    for f in fpl.fixtures(ttl):
        if f["event"] is None or f["team_h_score"] is None:
            continue
        for side, against in (("team_h", "team_a_score"), ("team_a", "team_h_score")):
            club = out.setdefault(teams[f[side]], {})
            club[f["event"]] = club.get(f["event"], 0) + f[against]
    return out


def audit_fixtures(ttl=fpl.CACHE_TTL):
    """Fixtures where the attributed goals don't add up to the scoreline.

    For each side: its players' goals plus the opposition's own goals must
    equal the goals it scored. This catches a goal that has been dropped or
    double-counted upstream.

    It does NOT catch a goal credited to the wrong player on the right team
    -- the Bogle/Calvert-Lewin case -- because the totals still balance.
    Nothing in the API can catch that; only the league's own ruling can,
    which is what `adjustments` in teams.json is for.
    """
    teams = {t["id"]: t["name"] for t in fpl.bootstrap(ttl)["teams"]}
    problems = []
    for f in fpl.fixtures(ttl):
        if f["event"] is None or f["team_h_score"] is None:
            continue
        scored = {"h": {}, "a": {}}
        for stat in f["stats"]:
            if stat["identifier"] in ("goals_scored", "own_goals"):
                for side in ("h", "a"):
                    total = sum(e["value"] for e in stat[side])
                    scored[side][stat["identifier"]] = total
        for side, other in (("h", "a"), ("a", "h")):
            credited = (scored[side].get("goals_scored", 0)
                        + scored[other].get("own_goals", 0))
            actual = f["team_%s_score" % side]
            if credited != actual:
                problems.append({
                    "gameweek": f["event"],
                    "fixture": "%s v %s" % (teams[f["team_h"]], teams[f["team_a"]]),
                    "team": teams[f["team_%s" % side]],
                    "scored": actual,
                    "credited": credited,
                })
    return problems


def _squad_at(squads, gameweek):
    """The picks in force that gameweek, or None if they aren't recorded.

    Squads are kept as a history so that re-running an old gameweek scores
    the players who were actually on the pitch, not today's line-up.
    """
    live = [s for s in squads if s["from"] <= gameweek]
    return max(live, key=lambda s: s["from"]) if live else None


def goals_by_gameweek(weeks, adjustments=(), ttl=fpl.CACHE_TTL):
    """{gameweek: {player id: goals}} for the whole league.

    This is the ONLY place goals are read. An earlier version counted them
    here from per-player histories and in the robot from /event/live, which
    are separate endpoints with separate cache lifetimes -- during a live
    gameweek the two drifted and the table disagreed with itself.
    """
    out = {}
    for gw in weeks:
        out[gw] = {e["id"]: e["stats"]["goals_scored"]
                   for e in fpl.live(gw, ttl) if e["stats"]["goals_scored"]}
    for adj in adjustments:
        gw, n = adj["gameweek"], adj["goals"]
        if gw not in out:
            continue
        out[gw][adj["from"][0]] = out[gw].get(adj["from"][0], 0) - n
        out[gw][adj["to"][0]] = out[gw].get(adj["to"][0], 0) + n
    return out


def own_goals_by_gameweek(weeks, ttl=fpl.CACHE_TTL):
    """Every own goal in the league, per gameweek, from the same source."""
    return {gw: sum(e["stats"]["own_goals"] for e in fpl.live(gw, ttl)) for gw in weeks}


def apply_adjustments(goals, adjustments):
    """Move re-attributed goals between players, in place.

    The API carries whatever attribution the dubious-goals panel last
    published, which can lag the league's own ruling. The league's ruling
    is what the table is scored on, so it wins here.
    """
    for adj in adjustments:
        gw, n = adj["gameweek"], adj["goals"]
        taken, given = adj["from"][0], adj["to"][0]
        goals.setdefault(taken, {})[gw] = goals.get(taken, {}).get(gw, 0) - n
        goals.setdefault(given, {})[gw] = goals.get(given, {}).get(gw, 0) + n
    return goals


def build(ttl=fpl.CACHE_TTL):
    """Per-gameweek results for every manager, plus the league table."""
    league = load_league()
    first = league["first_gameweek"]
    pts = league["points"]
    adjustments = league.get("adjustments", [])

    status = fpl.gameweeks(ttl)
    conceded = conceded_by_club(ttl)

    # Gameweeks with any result at all, from the league's own start.
    played = sorted({gw for club in conceded.values() for gw in club if gw >= first})
    by_gw = goals_by_gameweek(played, adjustments, ttl)
    own = own_goals_by_gameweek(played, ttl)
    goals = {}
    for gw, scorers in by_gw.items():
        for pid, n in scorers.items():
            goals.setdefault(pid, {})[gw] = n

    managers = []
    for name, m in league["managers"].items():
        weeks, unscorable = [], []
        for gw in played:
            squad = _squad_at(m["squads"], gw)
            if squad is None:
                unscorable.append(gw)
                continue
            gs = sum(own.get(gw, 0) if pick == OWN_GOALS
                     else goals.get(pick[0], {}).get(gw, 0)
                     for pick in squad["picks"])
            ga = (conceded.get(m["gk_club"], {}).get(gw, 0)
                  + conceded.get(m["def_club"], {}).get(gw, 0))
            weeks.append({
                "gameweek": gw,
                "gs": gs,
                "ga": ga,
                "result": "W" if gs > ga else ("D" if gs == ga else "L"),
                "provisional": not status.get(gw, {}).get("finished", False),
                "scorers": [
                    {"name": "Own Goals" if pick == OWN_GOALS else pick[1],
                     "goals": own.get(gw, 0) if pick == OWN_GOALS
                              else goals.get(pick[0], {}).get(gw, 0)}
                    for pick in squad["picks"]
                ],
            })
        managers.append({
            "manager": name,
            "gk_club": m["gk_club"],
            "def_club": m["def_club"],
            "budget": m["budget"],
            "weeks": weeks,
            "unscorable": unscorable,
        })

    badges = {t["name"]: {"code": t["code"], "short": t["short_name"]}
              for t in fpl.bootstrap(ttl)["teams"]}
    return {
        "season": league["season"],
        "champion": league.get("previous_champion"),
        "clubs": badges,
        "first_gameweek": first,
        "points": pts,
        "played": played,
        "provisional": [gw for gw in played if not status.get(gw, {}).get("finished")],
        "adjustments": adjustments,
        "managers": managers,
    }


def table(data, weeks=None):
    """Managers ranked over `weeks` (default: every gameweek with results)."""
    want = set(data["played"] if weeks is None else weeks)
    pts = data["points"]
    rows = []
    for m in data["managers"]:
        played = [w for w in m["weeks"] if w["gameweek"] in want]
        tally = {r: sum(1 for w in played if w["result"] == r) for r in "WDL"}
        gs = sum(w["gs"] for w in played)
        ga = sum(w["ga"] for w in played)
        rows.append({
            "manager": m["manager"],
            "gk_club": m["gk_club"],
            "def_club": m["def_club"],
            "pts": tally["W"] * pts["win"] + tally["D"] * pts["draw"] + tally["L"] * pts["loss"],
            "gs": gs, "ga": ga, "gd": gs - ga,
            "w": tally["W"], "d": tally["D"], "l": tally["L"],
            "played": len(played),
            "missing": sorted(set(want) & set(m["unscorable"])),
        })
    # PTS, then goals scored, then goal difference. The league's own sheet
    # is not consistent below PTS, so this may order ties differently.
    rows.sort(key=lambda r: (-r["pts"], -r["gs"], -r["gd"]))
    for rank, r in enumerate(rows, 1):
        r["rank"] = rank
    return rows


if __name__ == "__main__":
    import sys

    data = build()
    weeks = [int(a) for a in sys.argv[1:]] or data["played"]
    print("%s -- GW%s%s\n" % (data["season"], ",".join(map(str, weeks)),
                              "  (* provisional)" if data["provisional"] else ""))
    print("  %-4s %-11s %4s %4s %4s %4s %3s %3s %3s" %
          ("#", "manager", "PTS", "GS", "GA", "GD", "W", "L", "D"))
    for r in table(data, weeks):
        print("  %-4d %-11s %4d %4d %4d %+4d %3d %3d %3d%s" %
              (r["rank"], r["manager"], r["pts"], r["gs"], r["ga"], r["gd"],
               r["w"], r["l"], r["d"],
               "   missing GW%s" % ",".join(map(str, r["missing"])) if r["missing"] else ""))
