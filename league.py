"""CLI: inspect how a player scores under the current rules.

  python3 league.py player "Joao Pedro"
  python3 league.py owngoals
"""
import sys
import unicodedata

import fpl
import scoring


def _norm(s):
    """Fold accents so 'Joao Pedro' matches 'Joao Pedro'."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower())
        if not unicodedata.combining(c)
    )


def find(name):
    needle = _norm(name)
    matches = [p for p in fpl.players() if needle in _norm(p["name"])]
    if not matches:
        sys.exit("No player matching %r" % name)
    return matches


def show_player(name):
    rules = scoring.load_rules()
    for p in find(name):
        history = fpl.player_gameweeks(p["id"])
        total, per_gw = scoring.score_season(history, p["position"], rules)
        print("\n%s  (%s, %s)" % (p["name"], p["position"], p["team_name"]))
        print("  season: goals=%d assists=%d own_goals=%d mins=%d"
              % (p["goals_scored"], p["assists"], p["own_goals"], p["minutes"]))
        print("  custom points: %s   (FPL's own: %s)" % (total, p["total_points"]))
        print("  per gameweek:")
        for row in history:
            points, breakdown = scoring.score_gameweek(row, p["position"], rules)
            items = ", ".join("%s %+g" % (k, v) for k, v in breakdown.items())
            flag = "  <-- OWN GOAL" if row.get("own_goals") else ""
            print("    GW%-2d %6s  %s%s" % (row["round"], points, items, flag))


def show_owngoals():
    """Every own goal this season, and what it costs under the rules."""
    rules = scoring.load_rules()
    scorers = [p for p in fpl.players() if p["own_goals"] > 0]
    print("%d players have scored an own goal this season\n" % len(scorers))
    for p in sorted(scorers, key=lambda p: -p["own_goals"]):
        weights = scoring.weights_for(rules, p["position"])
        cost = p["own_goals"] * weights.get("own_goals", 0)
        print("  %-18s %-4s %-4s OG=%d  goals=%d  OG cost: %+g"
              % (p["name"], p["team_name"], p["position"],
                 p["own_goals"], p["goals_scored"], cost))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "owngoals"
    if cmd == "player":
        show_player(" ".join(sys.argv[2:]))
    elif cmd == "owngoals":
        show_owngoals()
    else:
        sys.exit(__doc__)
