"""Checks on the league rules, the roster file, and the rollup.

The scoring rules here were reverse-engineered from the league's own
spreadsheet, so the important tests are the ones that re-derive it: if an
upstream change or a bad edit breaks the model, these fail rather than
quietly producing a plausible-looking wrong table.
"""

import fpl
import standings

# The league's published table through GW6, read off the standings sheet.
PUBLISHED = {  # manager: (WK, Result, PTS, GS, GA, GD, W, L, D)
    "Jake": (1, 5, 7, 8, 9, -1, 2, 1, 1),   "Kirk": (0, 2, 6, 4, 5, -1, 2, 2, 0),
    "Sophie": (3, 3, 5, 11, 12, -1, 1, 1, 2), "Ziqi": (2, 2, 5, 6, 6, 0, 1, 1, 2),
    "Juha": (2, 3, 4, 10, 13, -3, 1, 2, 1), "Jon": (2, 3, 4, 8, 11, -3, 1, 2, 1),
    "Donal": (1, 4, 4, 6, 9, -3, 1, 2, 1),  "Danny": (2, 4, 3, 6, 11, -5, 1, 3, 0),
    "Jake's Dad": (1, 3, 3, 6, 9, -3, 1, 3, 0), "Xenia": (2, 3, 3, 6, 13, -7, 1, 3, 0),
    "Jason": (0, 5, 3, 3, 9, -6, 1, 3, 0),  "Callum": (0, 4, 2, 3, 9, -6, 0, 2, 2),
    "Quentin": (1, 6, 1, 5, 16, -11, 0, 3, 1), "James": (1, 7, 0, 2, 16, -14, 0, 4, 0),
    "Laura": (0, 3, 0, 0, 12, -12, 0, 4, 0),
}
SEASON_SO_FAR = [3, 4, 5, 6]


def _weeks(data, manager):
    return {w["gameweek"]: w for m in data["managers"] if m["manager"] == manager
            for w in m["weeks"]}


def test_goals_against_matches_the_published_table():
    """GA = the GK club's goals conceded plus the DEF club's, GW3-6.

    This is the fact the whole model rests on, and it is over-determined:
    15 managers share only 12 clubs between them.
    """
    data = standings.build()
    conceded = standings.conceded_by_club()
    for manager, m in standings.load_league()["managers"].items():
        got = sum(conceded[m["gk_club"]].get(gw, 0) + conceded[m["def_club"]].get(gw, 0)
                  for gw in SEASON_SO_FAR)
        assert got == PUBLISHED[manager][4], (
            "%s: GK %s + DEF %s conceded %d over GW3-6, table says %d"
            % (manager, m["gk_club"], m["def_club"], got, PUBLISHED[manager][4]))


def test_week_column_is_gameweek_six_goals_scored():
    """The sheet's 'WK' column is GW6 alone -- it pins every squad pick."""
    data = standings.build()
    for manager, published in PUBLISHED.items():
        week = _weeks(data, manager).get(6)
        assert week, "%s has no GW6" % manager
        assert week["gs"] == published[0], (
            "%s: GW6 goals %d, sheet says %d" % (manager, week["gs"], published[0]))


def test_result_column_is_gameweek_six_goals_conceded():
    data = standings.build()
    for manager, published in PUBLISHED.items():
        assert _weeks(data, manager)[6]["ga"] == published[1], manager


def test_published_table_is_internally_consistent():
    """GD = GS - GA, PTS = 3W+1D, and everyone has played the same number."""
    for manager, (_, _, pts, gs, ga, gd, w, l, d) in PUBLISHED.items():
        assert gd == gs - ga, manager
        assert pts == 3 * w + d, manager
        assert w + l + d == len(SEASON_SO_FAR), manager


def test_managers_do_not_play_each_other():
    """Total GS != total GA, which rules out head-to-head fixtures."""
    gs = sum(p[3] for p in PUBLISHED.values())
    ga = sum(p[4] for p in PUBLISHED.values())
    assert gs != ga, "if these matched, the league would be head-to-head"


def test_every_squad_has_seven_scorers():
    for manager, m in standings.load_league()["managers"].items():
        for squad in m["squads"]:
            assert len(squad["picks"]) == 7, "%s from GW%s" % (manager, squad["from"])


def test_transfers_change_at_most_three_outfield_picks():
    """A window moves up to three players -- never the GK or DEF club.

    A window the league has explicitly waived is skipped. GW10 changed six
    clubs and more than three picks by agreement, and the waiver is
    recorded on the window itself, so this guard still holds for every
    other window rather than being deleted outright.
    """
    league = standings.load_league()
    waived = {w["from"] for w in league.get("windows", []) if w.get("rules_waived")}
    for w in league.get("windows", []):
        if w.get("rules_waived"):
            assert w.get("_note"), "a waived window must record why: GW%d" % w["from"]
    for manager, m in league["managers"].items():
        assert len(m["squads"][0].get("transferred_in", [])) <= 3, manager
        squads = sorted(m["squads"], key=lambda s: s["from"])
        for before, after in zip(squads, squads[1:]):
            if after["from"] in waived:
                continue
            was = {tuple(p) if isinstance(p, list) else p for p in before["picks"]}
            now = {tuple(p) if isinstance(p, list) else p for p in after["picks"]}
            assert len(now - was) <= 3, "%s changed %d picks" % (manager, len(now - was))
            assert "gk_club" not in after and "def_club" not in after, (
                "%s changed a club in GW%d, which is not a waived window"
                % (manager, after["from"]))


def test_every_squad_player_exists():
    known = {p["id"] for p in fpl.players()}
    for manager, m in standings.load_league()["managers"].items():
        for squad in m["squads"]:
            for pick in squad["picks"]:
                if pick != standings.OWN_GOALS:
                    assert pick[0] in known, "%s: unknown id %s" % (manager, pick)


def test_clubs_resolve_against_the_api():
    names = {t["name"] for t in fpl.bootstrap()["teams"]}
    for manager, m in standings.load_league()["managers"].items():
        assert m["gk_club"] in names, "%s: %s" % (manager, m["gk_club"])
        assert m["def_club"] in names, "%s: %s" % (manager, m["def_club"])


def test_squad_history_is_ordered_and_starts_no_earlier_than_the_season():
    league = standings.load_league()
    for manager, m in league["managers"].items():
        froms = [s["from"] for s in m["squads"]]
        assert froms == sorted(froms), manager
        assert froms[0] >= league["first_gameweek"], manager


def test_every_week_is_scorable_now_that_opening_line_ups_are_recorded():
    data = standings.build()
    for row in standings.table(data):
        assert row["missing"] == [], "%s: %s" % (row["manager"], row["missing"])
        assert row["played"] == len(data["played"]), row["manager"]


def test_computed_table_reproduces_the_published_one():
    """The whole table, GW3-6: points, goals, and the W/L/D record.

    This is the end-to-end check. It only passes once the opening line-ups,
    the transfer window and the re-attributed goal are all in place -- goals
    alone are not enough, because two different mistakes can produce the same
    GS and still put the wins in different weeks.
    """
    data = standings.build()
    for row in standings.table(data, SEASON_SO_FAR):
        _, _, pts, gs, ga, _, w, l, d = PUBLISHED[row["manager"]]
        got = (row["pts"], row["gs"], row["ga"], row["w"], row["l"], row["d"])
        assert got == (pts, gs, ga, w, l, d), (
            "%s: computed %s, sheet %s" % (row["manager"], got, (pts, gs, ga, w, l, d)))


def test_reassigned_goal_moves_and_does_not_multiply():
    """An adjustment is a transfer of credit: the league's total is unchanged."""
    league = standings.load_league()
    goals = {}
    for adj in league["adjustments"]:
        for side in ("from", "to"):
            goals.setdefault(adj[side][0], {})
    before = sum(sum(v.values()) for v in goals.values())
    standings.apply_adjustments(goals, league["adjustments"])
    assert sum(sum(v.values()) for v in goals.values()) == before


def test_adjustments_name_players_that_exist():
    known = {p["id"]: p["name"] for p in fpl.players()}
    for adj in standings.load_league()["adjustments"]:
        for side in ("from", "to"):
            pid, name = adj[side]
            assert pid in known, "%s: unknown id %d" % (side, pid)


def test_the_reassigned_goal_is_still_missing_from_the_api():
    """If the API ever catches up, the adjustment becomes double-counting.

    This fails the day FPL applies the re-attribution, which is exactly when
    the entry in teams.json needs removing.
    """
    for adj in standings.load_league()["adjustments"]:
        rows = fpl.player_gameweeks(adj["from"][0])
        scored = sum(r["goals_scored"] for r in rows if r["round"] == adj["gameweek"])
        assert scored >= adj["goals"], (
            "the API no longer credits %s with the GW%d goal -- drop the adjustment"
            % (adj["from"][1], adj["gameweek"]))


def test_in_progress_gameweek_is_flagged_provisional():
    data = standings.build()
    status = fpl.gameweeks()
    for m in data["managers"]:
        for w in m["weeks"]:
            assert w["provisional"] == (not status[w["gameweek"]]["finished"])


def test_results_follow_from_goals():
    data = standings.build()
    for m in data["managers"]:
        for w in m["weeks"]:
            expect = "W" if w["gs"] > w["ga"] else ("D" if w["gs"] == w["ga"] else "L")
            assert w["result"] == expect, (m["manager"], w["gameweek"])


def test_table_points_match_the_win_draw_record():
    data = standings.build()
    for row in standings.table(data):
        assert row["pts"] == 3 * row["w"] + row["d"], row["manager"]
        assert row["gd"] == row["gs"] - row["ga"], row["manager"]


def test_every_fixture_goal_is_accounted_for():
    """Attributed goals must reconcile with the scoreline, league-wide.

    This is the automated half of the cross-check: it catches a goal that
    has gone missing or been counted twice. It cannot catch a goal given to
    the wrong player on the correct team, which is why the league's own
    rulings are recorded as adjustments instead.
    """
    problems = standings.audit_fixtures()
    assert problems == [], problems


def test_goals_come_from_one_source_only():
    """The table and the bots must not read goals from different endpoints.

    They used to: the table counted from per-player histories while the
    robot used /event/{gw}/live. Those have separate cache lifetimes, so
    during a live gameweek the app disagreed with itself.
    """
    import robot
    assert robot.goals_by_week is standings.goals_by_gameweek
    assert robot.own_by_week is standings.own_goals_by_gameweek


def test_live_goals_agree_with_the_per_player_histories_when_settled():
    """On finished gameweeks the two endpoints must tell the same story;
    a difference there would mean a real upstream inconsistency rather
    than one of them simply being fresher."""
    data = standings.build()
    settled = [gw for gw in data["played"] if gw not in data["provisional"]]
    league = standings.load_league()
    adjusted = {(a["gameweek"], a["from"][0]) for a in league["adjustments"]} | \
               {(a["gameweek"], a["to"][0]) for a in league["adjustments"]}
    live = standings.goals_by_gameweek(settled)
    ids = sorted({p[0] for m in league["managers"].values()
                  for sq in m["squads"] for p in sq["picks"] if p != standings.OWN_GOALS})
    for pid in ids:
        per = {}
        for row in fpl.player_gameweeks(pid):
            per[row["round"]] = per.get(row["round"], 0) + row["goals_scored"]
        for gw in settled:
            if (gw, pid) in adjusted:
                continue
            assert per.get(gw, 0) == live[gw].get(pid, 0), (pid, gw)


def _fake_manager(**squad_extra):
    """A manager with one opening squad, for exercising the club history."""
    squad = {"from": 3, "picks": ["OWN"] * 7}
    squad.update(squad_extra)
    return {"gk_club": "Everton", "def_club": "Leeds", "budget": 70.0,
            "squads": [squad]}


def test_a_club_change_applies_only_from_its_own_gameweek():
    """Clubs are history too: a squad entry may override them from its week.

    The clubs were fixed for the season at first, so they live on the
    manager. Once one changes, the earlier weeks must keep the club that
    was actually fielded -- editing the manager-level pair instead
    rewrites every gameweek played so far, which looks plausible and is
    wrong.
    """
    m = _fake_manager()
    assert standings.clubs_at(m, 3) == ("Everton", "Leeds")
    assert standings.clubs_at(m, 99) == ("Everton", "Leeds")

    m["squads"].append({"from": 10, "picks": ["OWN"] * 7, "gk_club": "Brighton"})
    assert standings.clubs_at(m, 9) == ("Everton", "Leeds"), "an earlier week moved"
    assert standings.clubs_at(m, 10) == ("Brighton", "Leeds")
    assert standings.current_clubs(m) == ("Brighton", "Leeds")
    assert m["gk_club"] == "Everton", "the manager-level club must not be edited"


def test_a_club_change_is_inherited_by_later_squads():
    """A later window that changes only picks keeps the clubs already set."""
    m = _fake_manager()
    m["squads"].append({"from": 10, "picks": ["OWN"] * 7, "gk_club": "Brighton"})
    m["squads"].append({"from": 11, "picks": ["OWN"] * 7})          # picks only
    m["squads"].append({"from": 12, "picks": ["OWN"] * 7, "def_club": "Arsenal"})
    assert standings.clubs_at(m, 10) == ("Brighton", "Leeds")
    assert standings.clubs_at(m, 11) == ("Brighton", "Leeds"), \
        "the GK club was dropped by a later picks-only window"
    assert standings.clubs_at(m, 12) == ("Brighton", "Arsenal")
    assert standings.current_clubs(m) == ("Brighton", "Arsenal")


def test_club_history_does_not_rescore_played_weeks():
    """Adding a club override for a future week leaves the table alone."""
    data = standings.build()
    before = {(mg["manager"], w["gameweek"]): (w["gs"], w["ga"], w["result"])
              for mg in data["managers"] for w in mg["weeks"]}
    league = standings.load_league()
    nxt = max(data["played"]) + 1
    for m in league["managers"].values():
        m["squads"].append({"from": nxt, "picks": m["squads"][-1]["picks"],
                            "gk_club": "Brighton", "def_club": "Arsenal"})
    real = standings.load_league
    standings.load_league = lambda path=None: league
    try:
        after = standings.build()
    finally:
        standings.load_league = real
    moved = {k: (before[k], v) for k, v in
             {(mg["manager"], w["gameweek"]): (w["gs"], w["ga"], w["result"])
              for mg in after["managers"] for w in mg["weeks"]}.items()
             if k in before and before[k] != v}
    assert not moved, moved
