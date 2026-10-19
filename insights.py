"""Side numbers: club records, squad value, transfer hindsight, and facts.

Nothing here affects the table. It exists because most of what is
interesting about a league is not the standings -- it is who overpaid,
whose transfer backfired, and which pick everybody missed.
"""
import json
import os

import fpl
import robot
import standings


def club_stats(weeks, ttl=fpl.CACHE_TTL):
    """Per club over `weeks`: what it conceded, and how reliably."""
    conceded = standings.conceded_by_club(ttl)
    teams = {t["name"]: t["short_name"] for t in fpl.bootstrap(ttl)["teams"]}
    codes = {t["name"]: t["code"] for t in fpl.bootstrap(ttl)["teams"]}
    prices = robot.load_prices()
    out = {}
    for club, by_gw in conceded.items():
        played = [gw for gw in weeks if gw in by_gw]
        if not played:
            continue
        conc = [by_gw[gw] for gw in played]
        out[club] = {
            "club": club,
            "short": teams.get(club, club[:3].upper()),
            "code": codes.get(club),
            "conceded": sum(conc),
            "played": len(played),
            "clean_sheets": sum(1 for c in conc if c == 0),
            "worst": max(conc),
            "per_week": round(sum(conc) / len(played), 2),
            "price": (prices or {}).get("clubs", {}).get(club),
            "owners": [],
        }
    for name, m in standings.load_league()["managers"].items():
        for role in ("gk_club", "def_club"):
            if m[role] in out:
                out[m[role]]["owners"].append(name)
    for c in out.values():
        c["owners"] = sorted(set(c["owners"]))
    return out


def _pick_goals(picks, weeks, goals, own):
    return {
        (robot.OWN if p == robot.OWN else p[0]):
            sum(own.get(w, 0) if p == robot.OWN else goals.get(w, {}).get(p[0], 0)
                for w in weeks)
        for p in picks
    }


def squad_value(data, weeks, ttl=fpl.CACHE_TTL):
    """Goals per £m for each manager, plus their best and worst buy."""
    prices = robot.load_prices()
    if not prices:
        return []
    league = standings.load_league()
    goals = robot.goals_by_week(weeks, league.get("adjustments", []), ttl)
    own = robot.own_by_week(weeks, ttl)
    names = {p["id"]: p["name"] for p in fpl.players(ttl)}

    rows = []
    for mgr, m in league["managers"].items():
        # Goals have to be credited week by week against whichever squad was
        # actually held, or a manager who transferred gets their new picks'
        # earlier goals for free -- and the total stops matching the table.
        scored, held = {}, {}
        for gw in weeks:
            squad = standings._squad_at(m["squads"], gw)
            if not squad:
                continue
            for p in squad["picks"]:
                key = robot.OWN if p == robot.OWN else p[0]
                label = "Own Goals" if key == robot.OWN else p[1]
                held[key] = label
                scored[key] = scored.get(key, 0) + (
                    own.get(gw, 0) if key == robot.OWN else goals.get(gw, {}).get(key, 0))

        latest = standings._squad_at(m["squads"], max(weeks))
        spend = prices["clubs"][m["gk_club"]] + prices["clubs"][m["def_club"]]
        spend += sum(robot.price_of(robot.OWN if p == robot.OWN else p[0], prices)
                     for p in latest["picks"])

        picks = []
        for key, label in held.items():
            cost = robot.price_of(key, prices)
            # `goals` is what the pick returned WHILE THIS MANAGER HELD IT,
            # which is what their table position is built on. `goals_all` is
            # what the player scored over the same weeks regardless of who
            # owned him -- without it, someone sold before a hot streak looks
            # like a dud rather than a bad sale.
            everyone = sum(own.get(gw, 0) if key == robot.OWN
                           else goals.get(gw, {}).get(key, 0) for gw in weeks)
            picks.append({"name": names.get(key, label) if key != robot.OWN else "Own Goals",
                          "goals": scored[key], "goals_all": everyone,
                          "held_all_range": scored[key] == everyone,
                          "price": cost,
                          "per_m": round(scored[key] / cost, 2) if cost else 0})
        total = sum(scored.values())
        picks.sort(key=lambda d: (-d["per_m"], -d["goals"]))
        rows.append({
            "manager": mgr, "goals": total, "spend": round(spend, 1),
            "per_m": round(total / spend, 3) if spend else 0,
            "best": picks[0], "worst": picks[-1], "picks": picks,
            "blanks": [{"name": p["name"], "goals_all": p["goals_all"]}
                       for p in picks if p["goals"] == 0],
        })
    rows.sort(key=lambda r: -r["per_m"])
    return rows


def transfer_hindsight(data, ttl=fpl.CACHE_TTL):
    """For each manager who used the window: did the change actually pay?

    Scores the weeks since the transfer twice -- once with the squad they
    moved to, once with the one they left -- and reports the difference in
    points, which is the only currency the table deals in.
    """
    league = standings.load_league()
    conceded = standings.conceded_by_club(ttl)
    goals = robot.goals_by_week(data["played"], league.get("adjustments", []), ttl)
    own = robot.own_by_week(data["played"], ttl)

    out = []
    for mgr, m in league["managers"].items():
        if len(m["squads"]) < 2:
            continue
        old, new = m["squads"][0], m["squads"][-1]
        since = [gw for gw in data["played"] if gw >= new["from"]]
        if not since:
            continue
        def run(picks):
            pts = gs = 0
            for gw in since:
                s = sum(own.get(gw, 0) if p == robot.OWN else goals.get(gw, {}).get(p[0], 0)
                        for p in picks)
                a = (conceded.get(m["gk_club"], {}).get(gw, 0)
                     + conceded.get(m["def_club"], {}).get(gw, 0))
                pts += 3 if s > a else (1 if s == a else 0)
                gs += s
            return pts, gs
        kept_pts, kept_gs = run(old["picks"])
        got_pts, got_gs = run(new["picks"])
        was = {tuple(p) if isinstance(p, list) else p for p in old["picks"]}
        now = {tuple(p) if isinstance(p, list) else p for p in new["picks"]}
        out.append({
            "manager": mgr, "from": new["from"], "weeks": since,
            "in": sorted("Own Goals" if p == robot.OWN else p[1] for p in now - was),
            "out": sorted("Own Goals" if p == robot.OWN else p[1] for p in was - now),
            "points": got_pts, "points_if_kept": kept_pts,
            "goals": got_gs, "goals_if_kept": kept_gs,
            "swing": got_pts - kept_pts,
        })
    out.sort(key=lambda r: -r["swing"])
    return out


def ownership(data, weeks, ttl=fpl.CACHE_TTL):
    """Who owns whom, and what it returned -- the differentials live here."""
    league = standings.load_league()
    goals = robot.goals_by_week(weeks, league.get("adjustments", []), ttl)
    own = robot.own_by_week(weeks, ttl)
    names = {p["id"]: p["name"] for p in fpl.players(ttl)}

    held = {}
    for mgr, m in league["managers"].items():
        squad = standings._squad_at(m["squads"], max(weeks))
        for p in squad["picks"]:
            key = robot.OWN if p == robot.OWN else p[0]
            held.setdefault(key, []).append(mgr)
    rows = []
    for key, owners in held.items():
        g = sum(own.get(w, 0) if key == robot.OWN else goals.get(w, {}).get(key, 0)
                for w in weeks)
        rows.append({"name": "Own Goals" if key == robot.OWN else names.get(key, str(key)),
                     "owners": sorted(owners), "count": len(owners), "goals": g})
    rows.sort(key=lambda r: (-r["goals"], -r["count"]))
    return rows


def _owned_ids(owned, league):
    """Pair each ownership row back to the pick id it came from."""
    ids = {}
    for m in league["managers"].values():
        for squad in m["squads"]:
            for p in squad["picks"]:
                key = robot.OWN if p == robot.OWN else p[0]
                ids["Own Goals" if key == robot.OWN else p[1]] = key
    return [(o, ids[o["name"]]) for o in owned if o["name"] in ids]


def facts(data, weeks, ttl=fpl.CACHE_TTL):
    """One-line observations, generated from the season rather than written.

    Each is a (headline, detail) pair. They are recomputed on refresh, so
    nothing here goes stale or has to be maintained by hand.
    """
    league = standings.load_league()
    prices = robot.load_prices()
    rows = standings.table(data, weeks)
    clubs = club_stats(weeks, ttl)
    owned = ownership(data, weeks, ttl)
    value = squad_value(data, weeks, ttl)
    goals = robot.goals_by_week(weeks, league.get("adjustments", []), ttl)
    own = robot.own_by_week(weeks, ttl)
    out = []

    og = next((o for o in owned if o["name"] == "Own Goals"), None)
    if og and prices:
        rate = og["goals"] / prices["own_goals"]
        better = [o for o in owned if o["goals"] > og["goals"]]
        # Compare on the same gameweeks rather than asserting a ratio: over a
        # short range Own Goals can be level with a cheap striker, over a
        # season it is far ahead, and the sentence has to say which.
        rival = [(o["goals"] / robot.price_of(pid, prices), o["name"])
                 for o, pid in _owned_ids(owned, league)
                 if pid != robot.OWN and robot.price_of(pid, prices)]
        rival_rate, rival_name = max(rival) if rival else (0.0, "-")
        standing = ("ahead of" if rate > rival_rate + 0.005 else
                    "level with" if rate > rival_rate - 0.005 else "behind")
        out.append(("Own Goals returns %.2f goals per £m" % rate,
                    "%d goal%s for £%.1fm, which is %s the best owned player "
                    "(%s, %.2f). %d pick%s outscored it outright, and %d of %d "
                    "managers hold it."
                    % (og["goals"], "" if og["goals"] == 1 else "s",
                       prices["own_goals"], standing, rival_name, rival_rate,
                       len(better), "" if len(better) == 1 else "s",
                       og["count"], len(rows))))

    tight = min(clubs.values(), key=lambda c: (c["per_week"], -c["clean_sheets"]))
    out.append(("%s have been the safest defence" % tight["club"],
                "%d conceded in %d week%s, %d of them clean. %s"
                % (tight["conceded"], tight["played"], "" if tight["played"] == 1 else "s",
                   tight["clean_sheets"],
                   "Owned by " + ", ".join(tight["owners"]) + "." if tight["owners"]
                   else "Nobody owns them.")))

    leaky = max(clubs.values(), key=lambda c: c["per_week"])
    if leaky["owners"]:
        out.append(("%s have been the costliest club to own" % leaky["club"],
                    "%.2f conceded a week, worst single week %d. Owned by %s."
                    % (leaky["per_week"], leaky["worst"], ", ".join(leaky["owners"]))))

    blanks = sum(len(v["blanks"]) for v in value)
    if blanks:
        worst = max(value, key=lambda v: len(v["blanks"]))
        # A pick sold before it scored still counts as nothing for the manager,
        # but saying so flatly reads as "this player never scored". The bracket
        # is the player's real tally over the same weeks.
        listed = ", ".join(
            b["name"] if b["goals_all"] == 0 else "%s (%d elsewhere)" % (b["name"], b["goals_all"])
            for b in worst["blanks"])
        sold_well = sum(1 for v in value for b in v["blanks"] if b["goals_all"] > 0)
        held_total = sum(len(v["picks"]) for v in value)
        out.append(("%d of the %d picks held this season returned nothing to their owner"
                    % (blanks, held_total),
                    "%s carries the most with %d: %s.%s"
                    % (worst["manager"], len(worst["blanks"]), listed,
                       " %d of the league's blanks did score — just not while "
                       "that manager held them." % sold_well if sold_well else "")))

    solo = [o for o in owned if o["count"] == 1 and o["goals"] > 0]
    if solo:
        top = max(solo, key=lambda o: o["goals"])
        out.append(("The best differential is %s" % top["name"],
                    "%d goals, and only %s owns him."
                    % (top["goals"], top["owners"][0])))

    best_week, best_val = None, -1
    for m in data["managers"]:
        for w in m["weeks"]:
            if w["gameweek"] in weeks and w["gs"] > best_val:
                best_week, best_val = (m["manager"], w), w["gs"]
    if best_week:
        mgr, w = best_week
        out.append(("The biggest week belongs to %s" % mgr,
                    "%d goals in GW%d, finishing %d–%d."
                    % (w["gs"], w["gameweek"], w["gs"], w["ga"])))

    quiet = [w for w in weeks if own.get(w, 0) == 0]
    if quiet:
        out.append(("Own Goals has blanked in %d of %d weeks" % (len(quiet), len(weeks)),
                    "No own goals at all in GW%s — which is the risk you take."
                    % ", ".join(str(w) for w in quiet)))

    return out


def expected_goals(data, weeks, ttl=fpl.CACHE_TTL):
    """Each manager's goals against the expected goals their picks generated.

    xG is the chance-quality model: a tap-in counts for most of a goal, a
    speculative shot for very little. Scoring well above it usually means
    good finishing or good luck; well below it means chances are being
    created and missed. Over five gameweeks it is mostly luck.

    Own goals have no xG -- nobody intends them -- so an Own Goals pick is
    excluded from the expected column and flagged, rather than being
    silently counted as zero.
    """
    from concurrent.futures import ThreadPoolExecutor

    league = standings.load_league()
    goals = robot.goals_by_week(weeks, league.get("adjustments", []), ttl)
    own = robot.own_by_week(weeks, ttl)

    ids = sorted({p[0] for m in league["managers"].values() for sq in m["squads"]
                  for p in sq["picks"] if p != robot.OWN})
    with ThreadPoolExecutor(max_workers=8) as pool:
        hist = dict(zip(ids, pool.map(lambda i: fpl.player_gameweeks(i, ttl), ids)))
    xg = {}
    for pid, rows in hist.items():
        per = {}
        for r in rows:
            per[r["round"]] = per.get(r["round"], 0.0) + float(r.get("expected_goals") or 0)
        xg[pid] = per

    out = []
    for mgr, m in league["managers"].items():
        got = exp = og = 0.0
        has_own = False
        for gw in weeks:
            squad = standings._squad_at(m["squads"], gw)
            if not squad:
                continue
            for p in squad["picks"]:
                if p == robot.OWN:
                    has_own = True
                    og += own.get(gw, 0)
                    continue
                got += goals.get(gw, {}).get(p[0], 0)
                exp += xg.get(p[0], {}).get(gw, 0.0)
        out.append({
            "manager": mgr,
            "goals": int(got + og),
            "goals_from_play": int(got),
            "own_goals": int(og),
            "xg": round(exp, 2),
            "delta": round(got - exp, 2),
            "has_own_pick": has_own,
        })
    out.sort(key=lambda r: -r["delta"])
    return out


def palette(ttl=fpl.CACHE_TTL):
    """{light: [...], dark: [...]} -- manager colours, or None if absent."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "palette.json")
    if not os.path.exists(path):
        return None
    pal = json.load(open(path))
    return {"light": pal["light"], "dark": pal["dark"]}


def availability(ttl=fpl.CACHE_TTL):
    """Picks that are injured, suspended or otherwise not available.

    Squads are locked between transfer windows, so a pick ruled out for a
    month cannot be replaced -- which makes this worth seeing rather than
    discovering on a Saturday.
    """
    league = standings.load_league()
    els = {p["id"]: p for p in fpl.bootstrap(ttl)["elements"]}
    out = []
    for mgr, m in league["managers"].items():
        squad = m["squads"][-1]
        for pick in squad["picks"]:
            if pick == robot.OWN:
                continue
            e = els.get(pick[0])
            if not e or e["status"] == "a":
                continue
            out.append({
                "manager": mgr,
                "name": e["web_name"],
                "code": e["code"],
                "status": e["status"],
                "chance": e["chance_of_playing_next_round"],
                "news": e["news"] or "",
            })
    out.sort(key=lambda r: (r["chance"] if r["chance"] is not None else -1, r["manager"]))
    return out


def upcoming(count=5, ttl=fpl.CACHE_TTL):
    """The next few fixtures for every club, with the API's difficulty rating.

    The GK and DEF clubs are locked for the season, so the run they face
    is the one part of a squad nobody can do anything about.
    """
    teams = {t["id"]: t["name"] for t in fpl.bootstrap(ttl)["teams"]}
    short = {t["id"]: t["short_name"] for t in fpl.bootstrap(ttl)["teams"]}
    nxt = {}
    for f in sorted((f for f in fpl.fixtures(ttl)
                     if f["event"] and not f["finished"] and f["team_h_score"] is None),
                    key=lambda f: (f["event"], f["id"])):
        for side, other, diff in (("team_h", "team_a", "team_h_difficulty"),
                                  ("team_a", "team_h", "team_a_difficulty")):
            club = teams[f[side]]
            rows = nxt.setdefault(club, [])
            if len(rows) < count:
                rows.append({"gameweek": f["event"], "opponent": short[f[other]],
                             "home": side == "team_h", "difficulty": f[diff]})
    owners = {}
    for name, m in standings.load_league()["managers"].items():
        for role in ("gk_club", "def_club"):
            owners.setdefault(m[role], []).append(name)
    return {club: {"fixtures": rows,
                   "total": sum(r["difficulty"] for r in rows),
                   "owners": sorted(set(owners.get(club, [])))}
            for club, rows in nxt.items()}


def gameweek_shape(ahead=6, ttl=fpl.CACHE_TTL):
    """Clubs with no fixture, or two, in an upcoming gameweek.

    A blank means a guaranteed clean sheet for whoever owns them; a double
    means two chances to concede.
    """
    teams = {t["id"]: t["short_name"] for t in fpl.bootstrap(ttl)["teams"]}
    status = fpl.gameweeks(ttl)
    future = sorted(gw for gw, s in status.items() if not s["finished"])[:ahead]
    counts = {gw: {} for gw in future}
    for f in fpl.fixtures(ttl):
        if f["event"] in counts:
            for side in ("team_h", "team_a"):
                counts[f["event"]][teams[f[side]]] = counts[f["event"]].get(teams[f[side]], 0) + 1
    out = []
    for gw in future:
        blanks = sorted(c for c in teams.values() if counts[gw].get(c, 0) == 0)
        doubles = sorted(c for c, n in counts[gw].items() if n > 1)
        if blanks or doubles:
            out.append({"gameweek": gw, "blanks": blanks, "doubles": doubles})
    return out
