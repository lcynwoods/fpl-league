"""Thin client for the public Fantasy Premier League API.

No scraping, no auth. Responses are cached to disk so repeated runs and
experiments with scoring rules don't hammer the API.
"""
import json
import os
import tempfile
import time
import urllib.request

BASE = "https://fantasy.premierleague.com/api"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CACHE_TTL = 60 * 60  # seconds; stats only change after matches

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _get(path, cache_key, ttl=CACHE_TTL):
    cached = os.path.join(CACHE_DIR, cache_key + ".json")
    if os.path.exists(cached) and time.time() - os.path.getmtime(cached) < ttl:
        try:
            with open(cached) as fh:
                return json.load(fh)
        except ValueError:
            # Half-written by an older build, or truncated by a crash.
            # Re-fetching is always safe, so don't make it the caller's problem.
            pass

    req = urllib.request.Request(
        BASE + path, headers={"User-Agent": "fpl-league/0.1"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    _write_cache(cached, data)
    return data


def _write_cache(cached, data):
    """Write the cache file atomically.

    The app fires several requests at once and each fans out over threads,
    so a plain open(path, "w") truncates a file another thread is part-way
    through reading -- which surfaced as the table intermittently failing
    to load. Writing a temp file and renaming it means a reader always
    sees either the whole old file or the whole new one.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, cached)          # atomic on POSIX
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def bootstrap(ttl=CACHE_TTL):
    """Everything static-ish: all players, teams, gameweek calendar."""
    return _get("/bootstrap-static/", "bootstrap", ttl)


def player_gameweeks(player_id, ttl=CACHE_TTL):
    """One row of raw stats per gameweek the player has featured in."""
    return _get(
        "/element-summary/%d/" % player_id, "player-%d" % player_id, ttl
    )["history"]


def players(ttl=CACHE_TTL):
    """Season totals for every player, with position and team resolved."""
    data = bootstrap(ttl)
    teams = {t["id"]: t["short_name"] for t in data["teams"]}
    out = []
    for e in data["elements"]:
        row = dict(e)
        row["position"] = POSITIONS[e["element_type"]]
        row["team_name"] = teams[e["team"]]
        row["name"] = e["web_name"]
        out.append(row)
    return out


def fixtures(ttl=CACHE_TTL):
    """Every fixture with its scoreline -- the source for goals conceded."""
    return _get("/fixtures/", "fixtures", ttl)


def live(gameweek, ttl=CACHE_TTL):
    """Every player's stats for one gameweek, in a single request.

    Pulling per-player histories costs one call each; this costs one call
    for the whole league, which is what makes scanning all ~670 players
    for the best possible squad practical.
    """
    return _get("/event/%d/live/" % gameweek, "live-%d" % gameweek, ttl)["elements"]


def gameweeks(ttl=CACHE_TTL):
    """{id: {finished, provisional}} for every gameweek in the calendar.

    A gameweek that is under way reports scores for the matches played so
    far, so its numbers are real but incomplete.
    """
    out = {}
    for ev in bootstrap(ttl)["events"]:
        out[ev["id"]] = {
            "finished": ev["finished"] and ev["data_checked"],
            "current": ev["is_current"],
            "deadline": ev["deadline_time"],
        }
    return out


def current_gameweek(ttl=CACHE_TTL):
    for ev in bootstrap(ttl)["events"]:
        if ev["is_current"]:
            return ev["id"]
    return None
