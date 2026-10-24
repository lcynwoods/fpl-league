"""Transfer windows: opening one, checking a manager's changes, recording them.

A window lets a manager swap up to three of their seven scorers. The GK
and DEF clubs never change, and the squad has to stay inside the budget.
Windows come at irregular times, so the organiser opens and closes them;
a window names the gameweek the new squads apply from.

The squads in teams.json are a history, and this module only ever appends
to it. A squad for a gameweek that has started is never written, because
rescoring past weeks against a squad that was not on the pitch gives a
table that looks right and is wrong. Before its deadline a manager can
change their mind: resubmitting replaces that window's entry, and the
swaps are always counted from the squad held before the window.

Every function takes the league dict and returns or changes it; nothing
here touches the disk except save() and log().
"""
import datetime
import json
import os
import tempfile

import standings

OWN = standings.OWN_GOALS
MAX_SWAPS = 3
SQUAD_SIZE = 7
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transfers.log.jsonl")


class TransferError(Exception):
    """Every reason a change was refused, so the page can show all of them."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def _parse_deadline(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def started(gameweek, deadlines, played, now):
    """True once a gameweek's deadline has passed or it has any result."""
    if gameweek in played:
        return True
    deadline = deadlines.get(gameweek)
    return deadline is None or _parse_deadline(deadline) <= now


def current_window(league):
    """The open window, or None."""
    for w in league.get("windows", []):
        if w.get("status") == "open":
            return w
    return None


def open_window(league, gameweek, deadlines, played, now):
    """Open a window whose squads apply from `gameweek`."""
    errors = []
    if current_window(league):
        errors.append("GW%d's window is already open; close it first"
                      % current_window(league)["from"])
    if started(gameweek, deadlines, played, now):
        errors.append("GW%d has already started, so a squad for it would rescore "
                      "a week that was played with a different one" % gameweek)
    latest = max((s["from"] for m in league["managers"].values() for s in m["squads"]),
                 default=0)
    if gameweek <= latest:
        errors.append("a squad from GW%d is already recorded; a window must come after it"
                      % latest)
    if errors:
        raise TransferError(errors)
    window = {"from": gameweek, "status": "open", "opened": now.isoformat(timespec="seconds")}
    league.setdefault("windows", []).append(window)
    return window


def close_window(league, now):
    window = current_window(league)
    if not window:
        raise TransferError(["no window is open"])
    window["status"] = "closed"
    window["closed"] = now.isoformat(timespec="seconds")
    return window


def base_squad(manager, window):
    """The squad held going into the window: what swaps are counted from."""
    return standings._squad_at(manager["squads"], window["from"] - 1)


def _key(pick):
    return OWN if pick == OWN else pick[0]


def _norm(pick):
    """A submitted pick: "OWN" or a player id (int, or digits as a string)."""
    if pick == OWN:
        return OWN
    if isinstance(pick, bool):
        return None
    if isinstance(pick, int):
        return pick
    if isinstance(pick, str) and pick.isdigit():
        return int(pick)
    return None


def _price(pick, prices):
    return prices["own_goals"] if pick == OWN else prices["players"].get(pick)


def check(league, name, outs, ins, prices, names, deadlines, played, now):
    """Return (new picks, new budget, errors) for a proposed set of swaps.

    `names` maps every current-season player id to a display name; an id
    not in it is not a real player this season.

    The budget is checked by difference: the squad's stated cost, plus
    what comes in, minus what goes out. The price list is a draft that
    four opening squads do not reconcile against, so re-pricing the picks
    a manager kept would turn price-list errors into refused transfers.
    """
    errors = []
    window = current_window(league)
    manager = league["managers"].get(name)
    if manager is None:
        return None, None, ["no manager called %r" % name]
    if window is None:
        return None, None, ["no transfer window is open"]
    if started(window["from"], deadlines, played, now):
        errors.append("the GW%d deadline has passed; close the window" % window["from"])
    later = [s["from"] for s in manager["squads"] if s["from"] > window["from"]]
    if later:
        errors.append("%s already has a squad from GW%d, after this window" % (name, max(later)))

    base = base_squad(manager, window)
    if base is None:
        return None, None, errors + ["%s has no squad before GW%d" % (name, window["from"])]
    held = {_key(p): p for p in base["picks"]}

    outs, ins = [_norm(p) for p in outs], [_norm(p) for p in ins]
    if None in outs or None in ins:
        errors.append("each pick must be a player id or \"OWN\"")
        return None, None, errors
    if not ins and not outs:
        errors.append("no changes chosen")
    if len(outs) != len(ins):
        errors.append("%d out but %d in; each player out needs a replacement"
                      % (len(outs), len(ins)))
    if len(outs) > MAX_SWAPS:
        errors.append("%d changes; a window allows at most %d" % (len(outs), MAX_SWAPS))
    if len(set(outs)) != len(outs) or len(set(ins)) != len(ins):
        errors.append("the same player is listed twice")
    for p in outs:
        if p not in held:
            errors.append("%s is not in %s's squad" % (names.get(p, p), name))
    for p in ins:
        if p in held and p not in outs:
            errors.append("%s is already in %s's squad" % (names.get(p, p), name))
        elif p in outs:
            errors.append("%s is both out and in" % names.get(p, p))
        if p != OWN and p not in names:
            errors.append("player %s does not exist this season" % p)
        elif _price(p, prices) is None:
            errors.append("%s has no price in the league's list" % names.get(p, p))
    if errors:
        return None, None, errors

    # Keep the squad's order: each incoming pick takes its outgoing one's slot.
    swap = dict(zip(outs, ins))
    picks = []
    for p in base["picks"]:
        k = _key(p)
        if k in swap:
            n = swap[k]
            picks.append(OWN if n == OWN else [n, names[n]])
        else:
            picks.append(p)
    if len(picks) != SQUAD_SIZE:
        errors.append("the squad would have %d picks, not %d" % (len(picks), SQUAD_SIZE))

    stated = base.get("budget", manager["budget"])
    budget = round(stated + sum(_price(p, prices) for p in ins)
                   - sum(_price(p, prices) for p in outs), 1)
    if prices.get("budget") is not None and budget > prices["budget"] + 1e-9:
        errors.append("the squad would cost £%.1fm, over the £%.1fm budget"
                      % (budget, prices["budget"]))
    return picks, budget, errors


def record(league, name, outs, ins, prices, names, deadlines, played, now):
    """Write a manager's swaps for the open window into `league`.

    Returns (entry, replaced). Resubmitting before the deadline replaces
    that window's entry. Raises TransferError, changing nothing, if any
    rule fails.
    """
    picks, budget, errors = check(league, name, outs, ins, prices, names,
                                  deadlines, played, now)
    if errors:
        raise TransferError(errors)
    window = current_window(league)
    manager = league["managers"][name]
    base = base_squad(manager, window)
    was = {_key(p): p for p in base["picks"]}
    now_ = {_key(p): p for p in picks}
    entry = {
        "from": window["from"],
        "_note": "%d pick(s) changed in the window; recorded in the app" % len(set(was) - set(now_)),
        "recorded": now.isoformat(timespec="seconds"),
        "budget": budget,
        "out": [was[k] for k in was if k not in now_],
        "in": [now_[k] for k in now_ if k not in was],
        "picks": picks,
    }
    kept = [s for s in manager["squads"] if s["from"] != window["from"]]
    replaced = len(kept) != len(manager["squads"])
    manager["squads"] = kept + [entry]
    return entry, replaced


def withdraw(league, name, deadlines, played, now):
    """Drop a manager's entry for the open window, before its deadline."""
    window = current_window(league)
    if window is None:
        raise TransferError(["no transfer window is open"])
    if started(window["from"], deadlines, played, now):
        raise TransferError(["the GW%d deadline has passed" % window["from"]])
    manager = league["managers"][name]
    kept = [s for s in manager["squads"] if s["from"] != window["from"]]
    if len(kept) == len(manager["squads"]):
        raise TransferError(["%s has no transfers in this window" % name])
    manager["squads"] = kept


def save(league, path=standings.TEAMS_PATH):
    """Write teams.json atomically, in the layout it is kept in."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(league, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def log(event, path=LOG_PATH):
    """Append one line to the transfer log: a second copy of every change.

    The GW3-5 line-ups once survived only in a photograph. This file is
    never rewritten, so even an edit to teams.json leaves a trail.
    """
    with open(path, "a") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
