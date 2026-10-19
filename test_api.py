"""Live checks against the FPL API.

These need network, and exist to catch an upstream change -- a renamed or
dropped field would otherwise make the affected stat silently score zero.
Run after a gameweek: python test_api.py
"""
import fpl
import scoring


def test_every_scorable_stat_still_exists_upstream():
    history = None
    for player in fpl.players():
        if player["minutes"] > 0:
            history = fpl.player_gameweeks(player["id"])
            break
    assert history, "no player with minutes played"

    row = history[0]
    missing = sorted(s for s in scoring.SCORABLE_STATS if s not in row)
    assert not missing, "fields gone from the API: %s" % missing


def test_own_goals_is_present_and_numeric():
    """The field the whole league hinges on."""
    for player in fpl.players():
        assert isinstance(player["own_goals"], int), player["name"]


def test_positions_cover_every_player():
    for player in fpl.players():
        assert player["position"] in ("GKP", "DEF", "MID", "FWD")


def test_season_totals_match_summed_gameweeks():
    """Cross-check the two endpoints agree, for a player with an own goal."""
    og = [p for p in fpl.players() if p["own_goals"] > 0]
    assert og, "no own goals yet this season"
    p = og[0]
    history = fpl.player_gameweeks(p["id"])
    assert sum(r["own_goals"] for r in history) == p["own_goals"]
    assert sum(r["goals_scored"] for r in history) == p["goals_scored"]
    assert sum(r["minutes"] for r in history) == p["minutes"]


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  ok  %s" % name)
            passed += 1
    print("\n%d live tests passed" % passed)


def test_cache_writes_survive_concurrent_readers():
    """The app reads a cache file from several threads while another thread
    may be refreshing it. A non-atomic write truncates it mid-read, which
    showed up as the page intermittently failing to load."""
    import json as _json, os, tempfile, threading, time
    import fpl as _fpl

    blob = {"elements": [{"id": i} for i in range(3000)]}
    with tempfile.TemporaryDirectory() as tmp:
        old, _fpl.CACHE_DIR = _fpl.CACHE_DIR, tmp
        try:
            path = os.path.join(tmp, "probe.json")
            _fpl._write_cache(path, blob)
            stop, errors = [False], []

            def writer():
                while not stop[0]:
                    _fpl._write_cache(path, blob)

            def reader():
                while not stop[0]:
                    try:
                        with open(path) as fh:
                            _json.load(fh)
                    except ValueError as exc:
                        errors.append(str(exc))

            threads = [threading.Thread(target=writer)] + [
                threading.Thread(target=reader) for _ in range(3)]
            for t in threads:
                t.start()
            time.sleep(0.8)
            stop[0] = True
            for t in threads:
                t.join()
            assert not errors, "%d torn reads: %s" % (len(errors), errors[0])
        finally:
            _fpl.CACHE_DIR = old
