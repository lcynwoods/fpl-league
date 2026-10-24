"""Checks on transfer windows: the rules, and that past weeks never move.

Most of these run on a small made-up league so they need no network. The
last ones use the real league and the cached API, and pin the invariant
the handover warns about: recording a window must not rescore a gameweek
that has already been played.
"""
import copy
import datetime
import json
import os
import tempfile

import standings
import transfers

NOW = datetime.datetime(2026, 10, 19, 12, 0, tzinfo=datetime.timezone.utc)
DEADLINES = {6: "2026-10-10T10:00:00Z", 7: "2026-10-17T10:00:00Z",
             8: "2026-10-23T17:30:00Z", 9: "2026-10-31T11:00:00Z"}
PLAYED = {3, 4, 5, 6, 7}
NAMES = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F", 10: "Cheap", 11: "Dear",
         12: "Mid", 13: "Unpriced"}
PRICES = {"budget": 70.0, "own_goals": 5.0,
          "players": {1: 5.0, 2: 5.0, 3: 5.0, 4: 5.0, 5: 5.0, 6: 5.0,
                      10: 1.0, 11: 12.0, 12: 6.0}}
SQUAD = [[1, "A"], [2, "B"], [3, "C"], [4, "D"], [5, "E"], [6, "F"], "OWN"]


def _league(budget=69.0):
    return {"first_gameweek": 3, "windows": [],
            "managers": {"Sam": {"gk_club": "X", "def_club": "Y", "budget": budget,
                                 "squads": [{"from": 3, "picks": copy.deepcopy(SQUAD)}]}}}


def _open(league, gw=8):
    return transfers.open_window(league, gw, DEADLINES, PLAYED, NOW)


def _record(league, outs, ins, name="Sam", now=NOW):
    return transfers.record(league, name, outs, ins, PRICES, NAMES, DEADLINES, PLAYED, now)


def _errors(league, outs, ins, name="Sam", now=NOW):
    return transfers.check(league, name, outs, ins, PRICES, NAMES, DEADLINES, PLAYED, now)[2]


def test_a_window_cannot_open_on_a_gameweek_that_has_started():
    for gw in (6, 7):
        try:
            _open(_league(), gw)
        except transfers.TransferError as exc:
            assert "started" in str(exc)
        else:
            raise AssertionError("opened a window for GW%d" % gw)
    assert _open(_league(), 8)["status"] == "open"


def test_only_one_window_is_open_at_a_time():
    league = _league()
    _open(league)
    try:
        _open(league, 9)
    except transfers.TransferError as exc:
        assert "already open" in str(exc)
    else:
        raise AssertionError("opened a second window")
    transfers.close_window(league, NOW)
    assert transfers.current_window(league) is None


def test_nothing_is_accepted_without_an_open_window():
    assert _errors(_league(), [1], [10]) == ["no transfer window is open"]


def test_a_valid_window_appends_and_keeps_the_old_squad():
    league = _league()
    _open(league)
    entry, replaced = _record(league, [2], [12])
    squads = league["managers"]["Sam"]["squads"]
    assert not replaced and len(squads) == 2
    assert squads[0]["picks"] == SQUAD, "the old squad was edited"
    assert entry["from"] == 8
    assert entry["picks"][1] == [12, "Mid"], "the new pick should take the old one's slot"
    assert entry["out"] == [[2, "B"]] and entry["in"] == [[12, "Mid"]]
    assert entry["budget"] == 70.0 and entry["recorded"]


def test_more_than_three_swaps_is_refused():
    league = _league()
    _open(league)
    errs = _errors(league, [1, 2, 3, 4], [10, 11, 12, "OWN"])
    assert any("at most 3" in e for e in errs), errs


def test_swaps_must_pair_up():
    league = _league()
    _open(league)
    assert any("needs a replacement" in e for e in _errors(league, [1, 2], [10]))
    assert any("no changes" in e for e in _errors(league, [], []))


def test_players_must_be_real_held_new_and_priced():
    league = _league()
    _open(league)
    assert any("not in Sam's squad" in e for e in _errors(league, [10], [12]))
    assert any("already in Sam's squad" in e for e in _errors(league, [1], [2]))
    assert any("does not exist" in e for e in _errors(league, [1], [999]))
    assert any("no price" in e for e in _errors(league, [1], [13]))
    assert any("twice" in e for e in _errors(league, [1, 1], [10, 12]))
    assert any("player id" in e for e in _errors(league, ["Saka"], [10]))


def test_own_goals_can_only_be_held_once():
    league = _league()
    _open(league)
    assert any("already in" in e for e in _errors(league, [1], ["OWN"]))
    # but it can be sold and bought like any other pick
    assert _errors(league, ["OWN"], [12]) == []


def test_the_budget_is_checked_by_difference():
    """Stated 69.0, out 5.0, in 6.0 makes 70.0, which is fine; in 12.0 is not."""
    league = _league(budget=69.0)
    _open(league)
    assert _errors(league, [1], [12]) == []
    errs = _errors(league, [1], [11])
    assert any("over the" in e for e in errs), errs


def test_kept_picks_are_never_repriced():
    """A squad whose stated cost disagrees with the draft price list can still trade.

    Sam's six players at 5.0 plus Own Goals price out at 35.0, but the stated
    budget is 69.0. Only the difference counts, as it must while the price
    list is a draft that 4 opening squads don't reconcile against.
    """
    league = _league(budget=69.0)
    _open(league)
    entry, _ = _record(league, [1], [10])
    assert entry["budget"] == 65.0


def test_resubmitting_replaces_and_counts_from_the_squad_before_the_window():
    league = _league()
    _open(league)
    _record(league, [2, 3], [10, 12])
    entry, replaced = _record(league, [1], [12])
    squads = league["managers"]["Sam"]["squads"]
    assert replaced and len(squads) == 2
    assert entry["out"] == [[1, "A"]] and entry["in"] == [[12, "Mid"]]
    assert [2, "B"] in entry["picks"] and [3, "C"] in entry["picks"]


def test_withdrawing_drops_the_entry():
    league = _league()
    _open(league)
    _record(league, [1], [10])
    transfers.withdraw(league, "Sam", DEADLINES, PLAYED, NOW)
    assert len(league["managers"]["Sam"]["squads"]) == 1


def test_nothing_is_accepted_after_the_deadline():
    league = _league()
    _open(league)
    late = datetime.datetime(2026, 10, 23, 17, 30, tzinfo=datetime.timezone.utc)
    assert any("deadline has passed" in e for e in _errors(league, [1], [10], now=late))
    _record(league, [1], [10])
    try:
        transfers.withdraw(league, "Sam", DEADLINES, PLAYED, late)
    except transfers.TransferError:
        pass
    else:
        raise AssertionError("withdrew after the deadline")


def test_a_refused_change_leaves_the_league_untouched():
    league = _league()
    _open(league)
    before = copy.deepcopy(league)
    try:
        _record(league, [1, 2, 3, 4], [10, 11, 12, "OWN"])
    except transfers.TransferError:
        pass
    assert league == before


def test_save_is_atomic_and_round_trips():
    league = _league()
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "teams.json")
    transfers.save(league, path)
    assert json.load(open(path)) == league
    assert os.listdir(folder) == ["teams.json"], "temp file left behind"


def test_the_log_only_appends():
    path = os.path.join(tempfile.mkdtemp(), "log.jsonl")
    transfers.log({"event": "a"}, path)
    transfers.log({"event": "b"}, path)
    assert [json.loads(l)["event"] for l in open(path)] == ["a", "b"]


def test_the_real_league_has_one_closed_window():
    league = standings.load_league()
    assert transfers.current_window(league) is None or \
        transfers.current_window(league)["from"] > 6
    assert league["windows"][0] == {"from": 6, "status": "closed",
                                    "_note": league["windows"][0]["_note"]}


# --- against the real league (uses the API cache) ---------------------------

def _real_window_league():
    """The real league with a window for the gameweek after the last played."""
    import robot
    league = standings.load_league()
    data = standings.build()
    gw = max(data["played"]) + 1
    far = {gw: "2999-01-01T00:00:00Z"}
    for w in league.get("windows", []):
        w["status"] = "closed"
    transfers.open_window(league, gw, far, set(data["played"]), NOW)
    return league, data, far, robot.load_prices()


def test_recording_a_window_does_not_rescore_any_played_week():
    import fpl
    league, data, far, prices = _real_window_league()
    names = {p["id"]: p["web_name"] for p in fpl.bootstrap()["elements"]}
    for name, m in league["managers"].items():
        base = transfers.base_squad(m, transfers.current_window(league))
        out_pick = next(p for p in base["picks"] if p != transfers.OWN)
        held = {p[0] for p in base["picks"] if p != transfers.OWN}
        cheapest = min((pid for pid in prices["players"] if pid in names and pid not in held),
                       key=lambda pid: prices["players"][pid])
        transfers.record(league, name, [out_pick[0]], [cheapest], prices, names,
                         far, set(data["played"]), NOW)

    real = standings.load_league
    standings.load_league = lambda path=None: league
    try:
        after = standings.build()
    finally:
        standings.load_league = real
    assert standings.table(after) == standings.table(data)
    for a, b in zip(after["managers"], data["managers"]):
        assert a["weeks"] == b["weeks"], a["manager"]


def test_hindsight_judges_each_window_on_its_own():
    """With a second window, the first is scored only until the second."""
    import insights
    league = standings.load_league()
    data = standings.build()
    m = league["managers"]["Kirk"]
    last = max(data["played"])
    # a second "window" at the last played week, re-using the opening squad
    m["squads"].append({"from": last, "picks": m["squads"][0]["picks"]})
    real = standings.load_league
    standings.load_league = lambda path=None: league
    try:
        rows = [r for r in insights.transfer_hindsight(data) if r["manager"] == "Kirk"]
    finally:
        standings.load_league = real
    by_from = {r["from"]: r for r in rows}
    assert set(by_from) == {6, last}
    assert by_from[6]["weeks"] == [gw for gw in data["played"] if 6 <= gw < last]
    assert by_from[last]["weeks"] == [last]
