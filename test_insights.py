"""Checks on the side statistics.

These are decoration, not the table, so the bar is that nothing here can
state something the standings contradict.
"""
import fpl
import insights
import robot
import standings

DATA = standings.build()
WEEKS = DATA["played"]


def test_club_conceded_matches_the_standings():
    """A club's record here must agree with what it costs its owners."""
    clubs = insights.club_stats(WEEKS)
    for m in DATA["managers"]:
        gk, df = clubs.get(m["gk_club"]), clubs.get(m["def_club"])
        assert gk and df, m["manager"]
        assert gk["conceded"] + df["conceded"] == sum(w["ga"] for w in m["weeks"]), m["manager"]


def test_club_owners_are_accurate():
    clubs = insights.club_stats(WEEKS)
    league = standings.load_league()
    for club, c in clubs.items():
        expected = sorted({name for name, m in league["managers"].items()
                           if club in (m["gk_club"], m["def_club"])})
        assert c["owners"] == expected, club


def test_clean_sheets_never_exceed_weeks_played():
    for c in insights.club_stats(WEEKS).values():
        assert 0 <= c["clean_sheets"] <= c["played"]
        assert c["worst"] >= 0


def test_squad_value_goals_match_the_table():
    """Goals per manager must equal what the standings credit them with."""
    rows = {r["manager"]: r for r in standings.table(DATA, WEEKS)}
    for v in insights.squad_value(DATA, WEEKS):
        assert v["goals"] == rows[v["manager"]]["gs"], v["manager"]


def test_blanks_really_scored_nothing():
    """A manager who used the window held more than seven picks over the
    range, so the blank count is bounded by picks held, not by squad size."""
    league = standings.load_league()
    for v in insights.squad_value(DATA, WEEKS):
        held = {p[1] if p != robot.OWN else "Own Goals"
                for sq in league["managers"][v["manager"]]["squads"]
                for p in sq["picks"]}
        assert len(v["blanks"]) <= len(held), (v["manager"], len(v["blanks"]), held)
        assert v["best"]["goals"] >= v["worst"]["goals"]
        assert all(b["name"] in held for b in v["blanks"]), v["manager"]


def test_a_blank_reports_what_the_player_really_scored():
    """A pick sold before it scored must not read as "never scored"."""
    for v in insights.squad_value(DATA, WEEKS):
        for b in v["blanks"]:
            assert "goals_all" in b and b["goals_all"] >= 0, (v["manager"], b)
        for p in v["picks"]:
            # goals while owned can never exceed what the player scored overall
            assert p["goals"] <= p["goals_all"], (v["manager"], p)
            assert p["held_all_range"] == (p["goals"] == p["goals_all"])


def test_transfer_counterfactual_only_covers_weeks_after_the_window():
    for r in insights.transfer_hindsight(DATA):
        assert min(r["weeks"]) >= r["from"], r["manager"]
        assert r["swing"] == r["points"] - r["points_if_kept"]


def test_transfer_rows_name_real_changes():
    """in/out must be an actual difference between the two squads."""
    league = standings.load_league()
    for r in insights.transfer_hindsight(DATA):
        squads = league["managers"][r["manager"]]["squads"]
        was = {p[1] if p != robot.OWN else "Own Goals" for p in squads[0]["picks"]}
        now = {p[1] if p != robot.OWN else "Own Goals" for p in squads[-1]["picks"]}
        assert set(r["in"]) == now - was, r["manager"]
        assert set(r["out"]) == was - now, r["manager"]


def test_ownership_covers_every_squad_place():
    rows = insights.ownership(DATA, WEEKS)
    assert sum(r["count"] for r in rows) == 7 * len(DATA["managers"])


def test_facts_are_generated_and_non_empty():
    out = insights.facts(DATA, WEEKS)
    assert len(out) >= 4
    for headline, detail in out:
        assert headline and detail
        assert not headline.endswith("."), headline


def test_the_own_goals_fact_states_the_comparison_it_measures():
    """It must not claim a lead it does not have on the chosen weeks."""
    for headline, detail in insights.facts(DATA, WEEKS):
        if "Own Goals returns" in headline:
            assert any(w in detail for w in ("ahead of", "level with", "behind")), detail
            return
    raise AssertionError("the Own Goals fact went missing")


def test_expected_goals_totals_match_the_table():
    rows = {r["manager"]: r for r in standings.table(DATA, WEEKS)}
    for r in insights.expected_goals(DATA, WEEKS):
        assert r["goals"] == rows[r["manager"]]["gs"], (r["manager"], r["goals"])
        assert r["goals"] == r["goals_from_play"] + r["own_goals"], r["manager"]


def test_own_goals_are_kept_out_of_expected_goals():
    """Nobody intends an own goal, so it has no xG and must not be modelled
    as a miss -- a manager holding the pick would look unlucky forever."""
    for r in insights.expected_goals(DATA, WEEKS):
        if r["has_own_pick"]:
            assert r["own_goals"] >= 0
            assert r["delta"] == round(r["goals_from_play"] - r["xg"], 2), r["manager"]
        else:
            assert r["own_goals"] == 0, r["manager"]


def test_palette_has_a_colour_for_every_manager():
    pal = insights.palette()
    assert pal, "palette.json is missing"
    for mode in ("light", "dark"):
        assert len(pal[mode]) >= len(DATA["managers"]), (
            "%d managers but only %d %s colours -- two would share"
            % (len(DATA["managers"]), len(pal[mode]), mode))


def test_palette_still_meets_its_own_floors():
    """Re-runs the separation check, so editing palette.json cannot quietly
    introduce two colours nobody can tell apart."""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(insights.__file__)), "tools"))
    import check_palette
    assert check_palette.check()


def test_unavailable_picks_are_really_in_a_squad():
    league = standings.load_league()
    for r in insights.availability():
        squad = league["managers"][r["manager"]]["squads"][-1]
        names = {p[1] for p in squad["picks"] if p != robot.OWN}
        assert r["name"] in names or any(r["name"] in n or n in r["name"] for n in names), r
        assert r["status"] != "a"


def test_upcoming_fixtures_are_sane():
    up = insights.upcoming(count=5)
    assert len(up) >= 20 - 2, len(up)
    for club, v in up.items():
        assert len(v["fixtures"]) <= 5, club
        gws = [f["gameweek"] for f in v["fixtures"]]
        assert gws == sorted(gws), club
        for f in v["fixtures"]:
            assert 1 <= f["difficulty"] <= 5, (club, f)
        assert v["total"] == sum(f["difficulty"] for f in v["fixtures"])


def test_every_owned_club_has_a_run_to_show():
    league = standings.load_league()
    up = insights.upcoming()
    for m in league["managers"].values():
        for role in ("gk_club", "def_club"):
            assert m[role] in up, m[role]
            assert up[m[role]]["owners"], m[role]
