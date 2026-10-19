# 7 Stars Fantasy Football

A league table for the 7 Stars league, computed from the public Fantasy
Premier League API. Nothing is typed in by hand: squads and prices are
data files, everything else is derived from match results.

    python3 app.py          # then open http://127.0.0.1:8000

No runtime dependencies — the standard library only. `pytest` is used for
the tests but they also run without it (see below).

## How the league scores

Each manager holds **one GK club and one DEF club for the whole season**,
plus **seven goal scorers** that a transfer window can change. Every
gameweek their scorers' goals (GS) are played off against the goals their
two clubs conceded (GA):

    GS > GA  win (3)      GS == GA  draw (1)      GS < GA  loss (0)

Managers are **not** drawn against each other, which is why the league's
total GS and total GA do not match. `"Own Goals"` is a pick like any
other and scores every own goal in the league that week. The season
starts at gameweek 3.

These rules were reverse-engineered from the league's own spreadsheet and
then verified against the API — `test_standings.py` re-derives them, so
if any of it is wrong the tests fail rather than the table quietly lying.

## The data files

| file | what it holds |
|---|---|
| `teams.json` | every squad, as a **history** — each entry applies from its `from` gameweek, so past weeks score the players who were actually picked. Also the clubs, budgets, and any goals the league has re-attributed. |
| `prices.json` | the league's own price list (players, clubs, Own Goals) and where each price came from: `pdf`, `derived` or `fpl`. |
| `palette.json` | one colour per manager. Edit freely, then run `python3 tools/check_palette.py`. |
| `rules.json` | unrelated to the league table — weights for the older per-player scoring CLI in `league.py`. |

### Recording a transfer window

Append a new entry to that manager's `squads`; **do not edit the old
one**, or past gameweeks get rescored with a squad that was not on the
pitch at the time.

```json
"squads": [
  { "from": 3, "picks": [...] },
  { "from": 6, "picks": [...] }
]
```

### When the league overrules the API

FPL's data can lag the Premier League's own rulings. The GW4 goal first
credited to Bogle and later given to Calvert-Lewin is the live example —
FPL still has the original attribution. Those go in `adjustments` in
`teams.json` and are applied before anything is counted. A test fails the
day FPL catches up, so the entry gets removed instead of double-counting.

## What's in the app

- **Standings** with form, crests, medals and rank movement; click a row
  for the week-by-week detail.
- **Robot manager** — two bots that pick with hindsight, so they are a
  ceiling rather than a rival. *Thrifty* finds the cheapest squad that
  wins the most weeks; *Maximiser* spends up for goal difference.
- **Value & hindsight** — goals per £m, goals against expected (xG), and
  whether each transfer actually paid.
- **Squad watch** — injured or suspended picks, and the next five
  fixtures for every owned club with difficulty ratings. Both matter
  because squads and clubs are locked between windows.
- **Facts**, generated from the season rather than written.

## Tests

    python3 -m pytest -q                 # if pytest is installed
    python3 -c "$(cat <<'PY'
    import importlib
    for mod in ("test_scoring","test_standings","test_robot","test_insights","test_assets"):
        m = importlib.import_module(mod)
        for n in sorted(x for x in dir(m) if x.startswith("test_")):
            getattr(m, n)(); print("ok", mod + "." + n)
    PY
    )"

`test_api.py` needs the network and checks the live API still looks the
way the rest of the code expects.

## Layout

    fpl.py          cached client for the FPL API (atomic cache writes)
    standings.py    the league table; the only place goals are read
    robot.py        the two optimisers
    insights.py     club records, value, xG, transfers, availability, facts
    app.py          local web server + the crest/photo cache
    index.html      the whole front end
    league.py       older CLI for per-player custom scoring (uses rules.json)
