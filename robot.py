"""The robot manager: the best squad that was actually available.

Hindsight only -- it picks after the goals are in, so its table position
is a ceiling, not a rival. Two questions it answers:

  * for one gameweek, the best XI anyone could have fielded
  * over a run of gameweeks, the best single squad held throughout
    (which is a different question, because a win is won week by week:
    four goals in one week beats nothing you did not need)

Budget: prices come from prices.json (the league's own list). Without that
file the robot picks freely and says so.

The best season squad is a small integer programme, and with scipy
installed it is solved as one: exact on points, then goal difference, then
goals, then the cheapest squad that gets all three, and quick for a whole
season. Without scipy it falls back to a pure-Python search that is just as
exact on POINTS but slows down about 3x with every gameweek, and which
records the cheapest squad reaching those points rather than the
highest-scoring one -- so there the goals and goal difference it reports
are a lower bound. Points are what the table is ranked on either way.
"""
import json
import os
from itertools import combinations

try:
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
except ImportError:  # the pure-Python search below still gives exact points
    milp = None

import fpl
import standings

PRICES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")
OWN = standings.OWN_GOALS


def load_prices(path=PRICES_PATH):
    """{"players": {id: cost}, "clubs": {name: cost}, "own_goals": cost} or None."""
    if not os.path.exists(path):
        return None
    raw = json.load(open(path))
    return {
        "players": {int(k): v for k, v in raw.get("players", {}).items()},
        "clubs": raw.get("clubs", {}),
        "own_goals": raw.get("own_goals", 0),
        "budget": raw.get("budget"),
        # where each price came from: the league's list, solved from a squad,
        # or borrowed from FPL. Surfaced on hover so an estimate is visible.
        "price_source": raw.get("price_source", {}),
    }


# One source of truth for goals, shared with the table -- see
# standings.goals_by_gameweek for why these are not computed twice.
goals_by_week = standings.goals_by_gameweek
own_by_week = standings.own_goals_by_gameweek


def _club_pairs(conceded, weeks, clubs, prices=None):
    """Every (gk, def) pair with its weekly goals-against.

    Without a budget, a pair that concedes at least as much as another in
    every week can never do better, so it is pruned. With a budget that
    pruning is wrong -- a leakier defence may be the one you can afford --
    so every pair is kept and the search sorts it out.
    """
    pairs = []
    for a, b in combinations(sorted(clubs), 2):
        ga = tuple(conceded.get(a, {}).get(w, 0) + conceded.get(b, {}).get(w, 0)
                   for w in weeks)
        pairs.append(((a, b), ga))
    if not prices:
        return [(pair, ga) for pair, ga in pairs
                if not any(all(o <= g for o, g in zip(other, ga)) and other != ga
                           for _, other in pairs)]

    # With a budget, a pair is only beaten by one that is both no dearer and
    # no leakier in any week -- so prune on the two together, not on goals
    # alone. That is exact, and it cuts the search enough to stay quick.
    priced = [(pair, ga, prices["clubs"].get(pair[0], 0) + prices["clubs"].get(pair[1], 0))
              for pair, ga in pairs
              if pair[0] in prices["clubs"] and pair[1] in prices["clubs"]]
    keep = []
    for pair, ga, cost in priced:
        if not any(ocost <= cost and all(o <= g for o, g in zip(oga, ga))
                   and (ocost, oga) != (cost, ga)
                   for _, oga, ocost in priced):
            keep.append((pair, ga))
    return keep


def price_of(pick, prices):
    return prices["own_goals"] if pick == OWN else prices["players"].get(pick)


def _candidates(goals, own, weeks, prices):
    """Pick -> per-week goal vector, with hopeless and duplicate picks dropped.

    A player the league never priced cannot be bought, so once a price
    list is loaded anyone missing from it is not a legal pick.
    """
    vectors = {}
    for pid in {p for gw in weeks for p in goals.get(gw, {})}:
        vec = tuple(goals.get(gw, {}).get(pid, 0) for gw in weeks)
        if any(vec) and not (prices and price_of(pid, prices) is None):
            vectors[pid] = vec
    if any(own.get(w, 0) for w in weeks):
        vectors[OWN] = tuple(own.get(w, 0) for w in weeks)

    # Identical records are interchangeable, so keep at most seven of each --
    # cheapest first when prices are known.
    by_vec = {}
    for pid, vec in vectors.items():
        by_vec.setdefault(vec, []).append(pid)
    out = []
    for vec, ids in by_vec.items():
        if prices:
            ids.sort(key=lambda p: price_of(p, prices))
        out.extend((pid, vec) for pid in ids[:7])
    return out


def _reach(cands):
    """reach[i][w][j]: the most goals j picks from cands[i:] can add in week w.

    An optimistic ceiling -- each week may use different picks -- which is
    what lets the search drop a squad that cannot catch the best so far.
    """
    weeks = len(cands[0][1]) if cands else 0
    top = [[] for _ in range(weeks)]
    reach = [None] * (len(cands) + 1)
    reach[len(cands)] = [[0] * 8 for _ in range(weeks)]
    for i in range(len(cands) - 1, -1, -1):
        for w in range(weeks):
            top[w] = sorted(top[w] + [cands[i][1][w]], reverse=True)[:7]
        reach[i] = [[sum(t[:j]) for j in range(8)] for t in top]
    return reach


def _cheapest_squads(cands, reach, ga, budget=None, floor=0):
    """Capped goals -> (cheapest seven picks reaching them, those picks).

    Tracking the CHEAPEST squad per state is what makes the budget
    correct: keeping the highest-scoring one instead would throw away an
    affordable squad in favour of one that cannot be bought.

    Each candidate is considered once, in turn -- the same shape as
    best_value -- so a squad is built in one order only, not in every
    order its seven picks could be drawn. And a part-built squad that
    could not reach `floor` points even with the best of what is left in
    every week is dropped: it can never be the answer.
    """
    caps = tuple(g + 1 for g in ga)
    weeks = range(len(ga))

    def hopeless(state, left, more):
        pts = 0
        for w in weeks:
            best = state[w] + left[w][more]
            pts += 3 if best > ga[w] else (1 if best == ga[w] else 0)
        return pts < floor

    # dp[slots][capped goals] = (spend, picks)
    dp = [{} for _ in range(8)]
    dp[0][tuple(0 for _ in caps)] = (0.0, ())
    for i, (pid, vec, price) in enumerate(cands):
        # What is left only shrinks, so a squad that cannot catch up now
        # never will: drop it for good rather than extend it again.
        if floor:
            for k in range(7):
                dp[k] = {s: v for s, v in dp[k].items()
                         if not hopeless(s, reach[i], 7 - k)}
        left = reach[i + 1]
        # Fill from the top down so this candidate is used at most once.
        for k in range(6, -1, -1):
            nxt = dp[k + 1]
            more = 6 - k
            for state, (spend, picks) in dp[k].items():
                cost = spend + price
                if budget is not None and cost > budget:
                    continue
                ns = tuple(min(s + v, c) for s, v, c in zip(state, vec, caps))
                if floor and hopeless(ns, left, more):
                    continue
                cur = nxt.get(ns)
                if cur is None or cost < cur[0]:
                    nxt[ns] = (cost, picks + (pid,))
    return dp[7]


def best_fixed(weeks, goals, own, conceded, clubs, prices=None, budget=None):
    """The best single squad to have held across `weeks`."""
    solve = _solve_fixed if milp is not None else _search_fixed
    return solve(weeks, goals, own, conceded, clubs, prices, budget)


def _solve_fixed(weeks, goals, own, conceded, clubs, prices=None, budget=None):
    """best_fixed as an integer programme, for scipy's milp.

    One 0/1 switch per pick and per club (seven picks, two clubs), and per
    week a "drew at least" and a "won" switch. Each week's margin -- goals
    from the picks less goals the two clubs let in -- must be at least 0
    for the first and at least 1 for the second, unless that switch is off.
    It is solved four times, each time holding the last answer as a floor:
    most points, then best goal difference, then most goals, then the
    cheapest squad that still has all three.
    """
    cands = _candidates(goals, own, weeks, prices)
    if prices:
        clubs = [c for c in clubs if c in prices["clubs"]]
    if len(cands) < 7 or len(clubs) < 2:
        return None
    n_p, n_c, n_w = len(cands), len(clubs), len(weeks)

    scored = np.array([vec for _, vec in cands], dtype=float).T          # weeks x picks
    let_in = np.array([[conceded.get(c, {}).get(w, 0) for c in clubs]
                       for w in weeks], dtype=float)                     # weeks x clubs
    cost = np.array([price_of(p, prices) if prices else 0 for p, _ in cands]
                    + [prices["clubs"][c] if prices else 0 for c in clubs], dtype=float)

    # Columns: picks | clubs | drew-at-least per week | won per week.
    n = n_p + n_c + 2 * n_w
    picks, club_cols = slice(0, n_p), slice(n_p, n_p + n_c)
    drew, won = slice(n_p + n_c, n_p + n_c + n_w), slice(n_p + n_c + n_w, n)

    def row(**parts):
        r = np.zeros(n)
        for part, values in parts.items():
            r[{"picks": picks, "clubs": club_cols, "drew": drew, "won": won}[part]] = values
        return r

    rows, lo, hi = [row(picks=1), row(clubs=1)], [7, 2], [7, 2]
    if budget is not None:
        rows.append(np.concatenate([cost, np.zeros(2 * n_w)]))
        lo.append(-np.inf)
        hi.append(budget)
    for w in range(n_w):
        # Big enough to switch the week off: no pair lets in more than this.
        big = 2 * let_in[w].max() + 1
        margin = row(picks=scored[w], clubs=-let_in[w])
        drew_w = margin.copy()
        drew_w[drew.start + w] = -big
        won_w = margin.copy()
        won_w[won.start + w] = -big
        rows += [drew_w, won_w]
        lo += [-big, 1 - big]
        hi += [np.inf, np.inf]

    goals_for = row(picks=scored.sum(axis=0))
    goal_diff = row(picks=scored.sum(axis=0), clubs=-let_in.sum(axis=0))
    stages = [row(drew=1, won=2), goal_diff, goals_for]

    x = None
    for i, aim in enumerate(stages + [-np.concatenate([cost, np.zeros(2 * n_w)])]):
        res = milp(-aim, integrality=np.ones(n), bounds=Bounds(0, 1),
                   constraints=LinearConstraint(np.array(rows), lo, hi))
        if res.x is None:
            if x is None:
                return None  # no squad fits at all
            break
        x = np.round(res.x)
        if i < len(stages):
            # Hold this stage at its best before optimising the next one.
            rows.append(aim)
            lo.append(aim @ x - 0.5)
            hi.append(np.inf)
    return _describe_fixed(x, cands, clubs, weeks, scored, let_in, cost)


def _describe_fixed(x, cands, clubs, weeks, scored, let_in, cost):
    n_p, n_c = len(cands), len(clubs)
    chosen = x[:n_p] > 0.5
    pair = x[n_p:n_p + n_c] > 0.5
    gk, df = sorted(c for c, on in zip(clubs, pair) if on)
    weekly = [int(v) for v in scored[:, chosen].sum(axis=1)]
    ga = [int(v) for v in let_in[:, pair].sum(axis=1)]
    return {"gk_club": gk, "def_club": df,
            "picks": [p for (p, _), on in zip(cands, chosen) if on],
            "ga": ga, "pts": sum(3 if s > g else (1 if s == g else 0)
                                 for s, g in zip(weekly, ga)),
            "gs": sum(weekly), "gd": sum(weekly) - sum(ga),
            "spend": round(float(cost[:n_p + n_c] @ x[:n_p + n_c]), 1),
            "weekly_gs": weekly}


def _search_fixed(weeks, goals, own, conceded, clubs, prices=None, budget=None):
    """best_fixed in pure Python, for when scipy is not installed.

    Points are won week by week, so goals beyond what a week needed are
    worth nothing -- capping each week at one more than its goals-against
    keeps the search space small without changing any result.
    """
    # Big scorers first, so the ceiling on what is left falls quickly.
    cands = sorted(((pid, vec, price_of(pid, prices) if prices else 0)
                    for pid, vec in _candidates(goals, own, weeks, prices)),
                   key=lambda c: -sum(c[1]))
    vectors = {pid: vec for pid, vec, _ in cands}
    reach = _reach(cands)

    pairs = []
    for order, ((gk, df), ga) in enumerate(_club_pairs(conceded, weeks, clubs, prices)):
        base = 0.0
        if prices:
            if gk not in prices["clubs"] or df not in prices["clubs"]:
                continue
            base = prices["clubs"][gk] + prices["clubs"][df]
            if budget is not None and base > budget:
                continue
        pairs.append((order, gk, df, ga, base))

    # The tightest defences are searched first: they set a high points
    # floor early, and every later pair is searched only for squads that
    # can at least match it. Ties still go to the pair listed first.
    pairs.sort(key=lambda p: sum(p[3]))
    best = None
    for order, gk, df, ga, base in pairs:
        floor = best[0][0] if best else 0
        left = None if budget is None else budget - base
        for state, (spend, picks) in _cheapest_squads(cands, reach, ga, left, floor).items():
            spend += base
            pts = sum(3 if s > g else (1 if s == g else 0) for s, g in zip(state, ga))
            gs = [sum(vectors[p][i] for p in picks) for i in range(len(weeks))]
            score = (pts, sum(gs) - sum(ga), sum(gs), -spend, -order)
            if best is None or score > best[0]:
                best = (score, {"gk_club": gk, "def_club": df, "picks": list(picks),
                                "ga": list(ga), "pts": pts, "gs": sum(gs),
                                "gd": sum(gs) - sum(ga), "spend": round(spend, 1),
                                "weekly_gs": gs})
    return best[1] if best else None


def best_value(weeks, goals, own, conceded, clubs, prices, budget):
    """The other bot: spend the lot, maximise goal difference over `weeks`.

    Where the thrifty bot stops as soon as it has the points, this one
    treats the budget as a target. It optimises the season aggregate
    (goals for minus goals against), not the week-by-week record, so it
    can post a huge goal difference and still drop a week it happened to
    blank in -- which is the honest trade-off between the two.
    """
    if not prices or budget is None:
        return None
    step = 0.5
    cap = int(round(budget / step))

    items = []
    for pid, vec in _candidates(goals, own, weeks, prices):
        items.append((pid, sum(vec), int(round(price_of(pid, prices) / step))))

    # (picks used, half-millions spent) -> (goals, squad). Carrying the squad
    # rather than back-pointers keeps a pick from being spent twice.
    dp = {(0, 0): (0, ())}
    for pid, gained, cost in items:
        for (k, c), (got, squad) in list(dp.items()):
            if k == 7 or c + cost > cap:
                continue
            key = (k + 1, c + cost)
            cur = dp.get(key)
            if cur is None or got + gained > cur[0]:
                dp[key] = (got + gained, squad + (pid,))

    # best goals for each spend level, and the cheapest way to reach it
    by_spend = {c: dp[(7, c)] for (k, c) in dp if k == 7}
    if not by_spend:
        return None

    best = None
    for (gk, df), ga in _club_pairs(conceded, weeks, clubs, prices):
        if gk not in prices["clubs"] or df not in prices["clubs"]:
            continue
        spent = int(round((prices["clubs"][gk] + prices["clubs"][df]) / step))
        room = cap - spent
        if room < 0:
            continue
        for c, (got, squad) in by_spend.items():
            if c > room:
                continue
            score = (got - sum(ga), got, -(c + spent))
            if best is None or score > best[0]:
                best = (score, (gk, df, ga, squad))
    if not best:
        return None
    gk, df, ga, picks = best[1]
    picks = list(picks)

    weekly = [sum(own.get(w, 0) if p == OWN else goals.get(w, {}).get(p, 0) for p in picks)
              for w in weeks]
    spend = (prices["clubs"][gk] + prices["clubs"][df]
             + sum(price_of(p, prices) for p in picks))
    return {"gk_club": gk, "def_club": df, "picks": picks, "ga": list(ga),
            "pts": sum(3 if s > g else (1 if s == g else 0) for s, g in zip(weekly, ga)),
            "gs": sum(weekly), "gd": sum(weekly) - sum(ga),
            "spend": round(spend, 1), "weekly_gs": weekly}


def best_week(gw, goals, own, conceded, clubs, prices=None, budget=None):
    """The best XI for a single gameweek."""
    return best_fixed([gw], goals, own, conceded, clubs, prices, budget)


def build(weeks=None, ttl=fpl.CACHE_TTL):
    league = standings.load_league()
    data = standings.build(ttl)
    weeks = weeks or data["played"]
    conceded = standings.conceded_by_club(ttl)
    clubs = sorted(conceded)
    goals = goals_by_week(weeks, league.get("adjustments", []), ttl)
    own = own_by_week(weeks, ttl)
    prices = load_prices()
    budget = prices["budget"] if prices else None

    # A club with no result yet in a half-played gameweek looks like it has
    # conceded nothing, which would hand the robot a free clean sheet.
    played_in = {gw: {c for c in clubs if gw in conceded.get(c, {})} for gw in weeks}

    per_week = {}
    for gw in weeks:
        pick = best_week(gw, goals, own, conceded, sorted(played_in[gw]),
                         prices, budget)
        if pick:
            pick["provisional"] = gw in data["provisional"]
        per_week[gw] = pick

    settled = [gw for gw in weeks if gw not in data["provisional"]]
    return {
        "weeks": weeks,
        "capped": bool(prices),
        "budget": budget,
        "unpriced_picks": sorted(
            {pick[1] for m in league["managers"].values() for sq in m["squads"]
             for pick in sq["picks"]
             if prices and pick != OWN and price_of(pick[0], prices) is None}),
        "provisional": [gw for gw in weeks if gw in data["provisional"]],
        "per_week": per_week,
        "fixed": best_fixed(weeks, goals, own, conceded,
                            sorted(set.intersection(*played_in.values())
                                   if played_in else clubs),
                            prices, budget),
        "fixed_settled": (best_fixed(settled, goals, own, conceded,
                                     sorted(set.intersection(*[played_in[g] for g in settled]))
                                     , prices, budget) if settled and settled != weeks else None),
        "value_per_week": {gw: best_value([gw], goals, own, conceded,
                                          sorted(played_in[gw]), prices, budget)
                           for gw in weeks},
        "value_fixed": best_value(weeks, goals, own, conceded,
                                  sorted(set.intersection(*played_in.values())
                                         if played_in else clubs), prices, budget),
    }


def name_of(pick, names):
    return "Own Goals" if pick == OWN else names.get(pick, str(pick))


if __name__ == "__main__":
    import sys

    weeks = [int(a) for a in sys.argv[1:]] or None
    names = {p["id"]: p["name"] for p in fpl.players()}
    out = build(weeks)
    print("ROBOT MANAGER%s\n" % ("" if out["capped"] else "   (no price list -- budget cap NOT applied)"))

    for gw, b in out["per_week"].items():
        print("GW%d best XI  %d-%d  %s%s" % (gw, b["gs"], b["ga"][0],
              {3: "win", 1: "draw", 0: "loss"}[b["pts"]],
              "   £%.1fm of £%.0fm" % (b["spend"], out["budget"]) if out["capped"] else ""))
        print("   clubs   %s + %s" % (b["gk_club"], b["def_club"]))
        print("   scorers %s\n" % ", ".join(sorted(name_of(p, names) for p in b["picks"])))

    f = out["fixed"]
    print("Best single squad held across GW%s:" % ",".join(map(str, out["weeks"])))
    print("   clubs   %s + %s" % (f["gk_club"], f["def_club"]))
    print("   scorers %s" % ", ".join(sorted(name_of(p, names) for p in f["picks"])))
    print("   PTS %d   GS %d   GA %d   GD %+d%s"
          % (f["pts"], f["gs"], sum(f["ga"]), f["gd"],
             "   £%.1fm of £%.0fm" % (f["spend"], out["budget"]) if out["capped"] else ""))
