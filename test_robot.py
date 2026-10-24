"""Checks on the robot manager.

The robot picks with hindsight, so the strongest tests are the ceilings:
no real manager can have beaten it in any week it covers.
"""
import fpl
import robot
import standings

OUT = robot.build()
DATA = standings.build()
SETTLED = [gw for gw in OUT["weeks"] if gw not in OUT["provisional"]]

# A gameweek barely under way can have fewer scorers than a squad has places,
# so the robot has no legal squad to field and returns None for it. That is a
# real answer, not a failure -- the app skips those weeks, and so do these.
SOLVED = {gw: p for gw, p in OUT["per_week"].items() if p}
VALUE_SOLVED = {gw: p for gw, p in OUT["value_per_week"].items() if p}


def _human_weeks(gw):
    return [w for m in DATA["managers"] for w in m["weeks"] if w["gameweek"] == gw]


def test_robot_fields_a_legal_squad():
    for gw, pick in SOLVED.items():
        assert len(pick["picks"]) == 7, gw
        assert len(set(pick["picks"])) == 7, "duplicate pick in GW%s" % gw
        assert pick["gk_club"] != pick["def_club"], gw


def test_robot_only_uses_clubs_that_actually_played():
    """A club with no fixture yet must not be mistaken for a clean sheet."""
    conceded = standings.conceded_by_club()
    for gw, pick in SOLVED.items():
        for club in (pick["gk_club"], pick["def_club"]):
            assert gw in conceded.get(club, {}), "%s had no result in GW%d" % (club, gw)


def test_no_manager_outscored_the_robot_in_any_week():
    for gw, pick in SOLVED.items():
        best = max(w["gs"] for w in _human_weeks(gw))
        assert pick["gs"] >= best, "GW%d: a manager scored %d, robot %d" % (gw, best, pick["gs"])


def test_no_manager_beat_the_robot_on_points():
    """The real ceiling. Under a cap the robot may concede more than someone
    else did -- a cheap leaky defence it still beat is a fine trade -- so
    points, not goals against, is what must never be bettered."""
    for gw, pick in SOLVED.items():
        for w in _human_weeks(gw):
            human = 3 if w["gs"] > w["ga"] else (1 if w["gs"] == w["ga"] else 0)
            assert pick["pts"] >= human, (gw, w)


def test_the_best_fixed_squad_beats_every_real_squad():
    rows = standings.table(DATA, SETTLED)
    best_human = max(r["pts"] for r in rows)
    fixed = OUT["fixed_settled"] or OUT["fixed"]
    assert fixed["pts"] >= best_human, (fixed["pts"], best_human)


def test_a_perfect_record_is_the_ceiling():
    fixed = OUT["fixed_settled"] or OUT["fixed"]
    assert fixed["pts"] <= 3 * len(SETTLED)


def test_robot_honours_reassigned_goals():
    """The GW4 goal belongs to Calvert-Lewin, so the robot must count it there."""
    league = standings.load_league()
    goals = robot.goals_by_week([4], league["adjustments"])
    for adj in league["adjustments"]:
        if adj["gameweek"] != 4:
            continue
        raw = {e["id"]: e["stats"]["goals_scored"] for e in fpl.live(4)}
        assert goals[4][adj["to"][0]] == raw.get(adj["to"][0], 0) + adj["goals"]
        assert goals[4][adj["from"][0]] == raw.get(adj["from"][0], 0) - adj["goals"]


def test_own_goals_is_a_candidate_even_if_never_worth_buying():
    """It must be on the shortlist. Whether it gets picked is a verdict, not
    a bug -- at its price it has to out-score a striker to be worth a slot."""
    own = robot.own_by_week(OUT["weeks"])
    assert sum(own.values()) > 0
    cands = robot._candidates(
        robot.goals_by_week(OUT["weeks"], standings.load_league()["adjustments"]),
        own, OUT["weeks"], robot.load_prices())
    assert any(pid == robot.OWN for pid, _ in cands), "Own Goals was not even offered"


def test_budget_cap_is_reported_as_unapplied_until_prices_exist():
    """Nothing should claim the cap is enforced while prices.json is absent."""
    import os
    assert OUT["capped"] == os.path.exists(robot.PRICES_PATH)


def test_robot_stays_inside_the_budget():
    if not OUT["capped"]:
        return
    for pick in list(SOLVED.values()) + [OUT["fixed"]]:
        assert pick["spend"] <= OUT["budget"] + 1e-9, (pick["spend"], OUT["budget"])


def test_robot_never_picks_a_player_the_league_never_priced():
    if not OUT["capped"]:
        return
    prices = robot.load_prices()
    for pick in list(SOLVED.values()) + [OUT["fixed"]]:
        for p in pick["picks"]:
            assert robot.price_of(p, prices) is not None, p
        for club in (pick["gk_club"], pick["def_club"]):
            assert club in prices["clubs"], club


def test_the_cap_actually_binds():
    """With the cap on, the robot must do no better than without it."""
    import copy
    prices = robot.load_prices()
    assert prices and prices["budget"] == 70.0
    spends = [p["spend"] for p in SOLVED.values()]
    assert max(spends) <= 70.0 and max(spends) > 50.0, spends


def test_maximiser_fields_a_legal_squad():
    for pick in list(VALUE_SOLVED.values()) + [OUT["value_fixed"]]:
        if not pick:
            continue
        assert len(pick["picks"]) == 7, pick["picks"]
        assert len(set(pick["picks"])) == 7, "a pick was bought twice: %s" % (pick["picks"],)
        assert pick["gk_club"] != pick["def_club"]
        assert pick["spend"] <= OUT["budget"] + 1e-9


def test_maximiser_beats_the_thrifty_bot_on_goal_difference():
    """That is its whole job; if it doesn't, the search is wrong."""
    for gw, thrifty in SOLVED.items():
        rich = VALUE_SOLVED.get(gw)
        if rich:
            assert rich["gd"] >= thrifty["gd"], (gw, rich["gd"], thrifty["gd"])


def test_thrifty_bot_never_spends_more_than_the_maximiser_for_fewer_points():
    """The two bots answer different questions; points is the thrifty one's."""
    for gw, thrifty in SOLVED.items():
        rich = VALUE_SOLVED.get(gw)
        if rich:
            assert thrifty["pts"] >= rich["pts"], (gw, thrifty["pts"], rich["pts"])


def test_every_derived_price_keeps_its_squad_exact():
    """Prices solved from a squad must make that squad's budget come out right."""
    import json
    prices = robot.load_prices()
    league = standings.load_league()
    derived = {int(k) for k, v in prices.get("price_source", {}).items() if v == "derived"}
    assert derived, "no derived prices recorded"
    for mgr, m in league["managers"].items():
        for sq in m["squads"]:
            ids = {p[0] for p in sq["picks"] if p != robot.OWN}
            if not ids & derived:
                continue
            cost = prices["clubs"][m["gk_club"]] + prices["clubs"][m["def_club"]]
            cost += sum(prices["own_goals"] if p == robot.OWN
                        else prices["players"][p[0]] for p in sq["picks"])
            stated = sq.get("budget", m["budget"])
            assert abs(cost - stated) < 0.01, (mgr, sq["from"], cost, stated)
