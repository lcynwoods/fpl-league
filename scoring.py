"""Scoring engine: a pure function from a raw stat line to a points total.

Scoring is deliberately kept separate from fetching. Raw stats are stored
as the API gives them, so changing the rules re-scores the whole season
without re-fetching anything.
"""
import json
import os

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")

# Per-gameweek stats the API actually returns, so a typo or an upstream
# rename is caught loudly instead of silently scoring zero. test_api.py
# checks this set against the live API.
SCORABLE_STATS = {
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "starts", "tackles", "recoveries",
    "clearances_blocks_interceptions", "defensive_contribution",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded",
}

# Derived stats this module computes rather than reading straight off the row.
SYNTHETIC_STATS = {"goals_conceded_per_2"}


class RulesError(ValueError):
    """A rules file refers to a stat that cannot be scored."""


def validate_rules(rules):
    """Reject unknown stat keys loudly.

    Without this, `rules.json` saying "own_goal" instead of "own_goals"
    costs nothing at load time and silently stops penalising own goals.
    """
    known = SCORABLE_STATS | SYNTHETIC_STATS
    blocks = [("default", rules.get("default", {}))]
    for pos, weights in rules.get("by_position", {}).items():
        blocks.append(("by_position.%s" % pos, weights))

    unknown = []
    for where, weights in blocks:
        for stat in weights:
            if stat not in known:
                unknown.append("%s.%s" % (where, stat))
    if unknown:
        raise RulesError(
            "unknown stat(s) in rules: %s\nscorable stats are: %s"
            % (", ".join(sorted(unknown)), ", ".join(sorted(known)))
        )
    return rules


def load_rules(path=RULES_PATH):
    with open(path) as fh:
        return validate_rules(json.load(fh))


def weights_for(rules, position):
    """Default weights overlaid with any position-specific overrides."""
    merged = dict(rules.get("default", {}))
    merged.update(rules.get("by_position", {}).get(position, {}))
    return merged


def score_gameweek(stats, position, rules):
    """Points for one player in one fixture.

    `stats` is a raw history row from the API. Returns (total, breakdown)
    so a disputed score can always be itemised.
    """
    weights = weights_for(rules, position)
    breakdown = {}

    minutes = stats.get("minutes", 0)
    app = rules.get("appearance", {})
    if minutes > 0 and app.get("any_minutes"):
        breakdown["appearance"] = app["any_minutes"]
    if minutes >= 60 and app.get("sixty_plus"):
        breakdown["60+ mins"] = app["sixty_plus"]

    for stat, weight in weights.items():
        if stat == "goals_conceded_per_2":
            # Conceding is scored per two goals, and only for players who
            # were on long enough to be held responsible.
            if minutes >= 60:
                points = (stats.get("goals_conceded", 0) // 2) * weight
                if points:
                    breakdown["goals_conceded"] = points
            continue

        count = stats.get(stat, 0)
        if count and weight:
            breakdown[stat] = count * weight

    return round(sum(breakdown.values()), 2), breakdown


def score_season(history, position, rules):
    """Total across every fixture, plus per-gameweek totals.

    A gameweek can hold more than one fixture for a player (rescheduled
    matches make a "double gameweek"), so points accumulate per round
    rather than overwriting.
    """
    per_gw = {}
    for row in history:
        points, _ = score_gameweek(row, position, rules)
        rnd = row["round"]
        per_gw[rnd] = round(per_gw.get(rnd, 0) + points, 2)
    return round(sum(per_gw.values()), 2), per_gw
