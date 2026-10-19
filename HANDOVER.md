# Handover

Where this project stands, what is known to be uncertain, and the things
that will bite you. Written October 2026, during gameweek 7 of Season 3.

## The short version

The app reproduces the league's published GW3–6 table **exactly** — all
fifteen managers, on points, goals for, goals against and the W/D/L
record. That is the strongest statement available about whether the rules
below are right, and `test_standings.py` re-derives it on every run.

75 tests. Five of them (`test_api.py`) need the network.

## The rules were reverse-engineered, not given

Nobody wrote these down for us. They were recovered from two photographed
spreadsheets and then checked against the API:

- **GA = your GK club's goals conceded + your DEF club's.** Solved as 15
  equations over 12 unknown clubs with zero contradictions, and GW3–6 is
  the only window that fits.
- **Managers do not play each other.** Total GS (84) ≠ total GA (160), so
  there are no fixtures between managers; each week your scorers play
  your own two clubs.
- **The sheet's `WK` and `Result` columns are gameweek 6 alone** — goals
  scored and goals conceded. Matching those 15/15 is also what confirmed
  the transcription of all 105 squad picks.

If someone ever produces the actual rulebook and it contradicts any of
this, trust the rulebook and expect tests to fail loudly. That is what
they are for.

## Open questions nobody has answered

1. **The tiebreak below points.** The league's own sheet is not
   consistent — Sophie is placed above Ziqi on worse goal difference, and
   Danny/Jake's Dad/Xenia are not in GD order either. This app sorts
   PTS → GS → GD, so ties may come out in a different order than the
   group expects. **Ask the organiser.**
2. **The price list is a draft.** `7SFF_Player_List_Season_3_FIRST_PASS_V3.pdf`
   is what `prices.json` was built from. Only 11 of 15 opening squads
   price out exactly; Danny is −1.5, Jake's Dad +0.5, Donal +1.0, James
   +2.0. Every individual price traces to a correctly-named row, so the
   likeliest explanation is that prices were revised after that draft.
   **A final price list would close this**, and the check is the same
   trick as before: if all 15 squads reconcile, the list is right.
3. **One price is a guess.** Tyler Dibling is absent from the list and
   cannot be solved from the squads (the algebra wants a negative), so he
   uses FPL's own valuation. `prices.json` records this as
   `price_source: "fpl"`. Four others (King, Kostoulas, Maitland-Niles,
   Vaz) were solved exactly from the squads and are marked `derived`.
4. **Own Goals looks badly underpriced at £5.0m.** It returns far more
   goals per £m than any outfield pick, and the group's own history has
   it beating the top scorer in 32 of 50 seasons. Analysis suggested
   £15–18m. **This is the group's decision, not a bug.**

## Things that will break, and when

- **The Bogle → Calvert-Lewin adjustment must be deleted when FPL catches
  up.** A panel re-awarded that GW4 goal (confirmed by Leeds, 16 Sep
  2026) but FPL still has the original attribution, so `teams.json`
  carries an `adjustments` entry that corrects it.
  `test_the_reassigned_goal_is_still_missing_from_the_api` fails the day
  FPL applies it — **that failure is the signal to remove the entry**,
  not a bug to work around.
- **Transfer windows are appended, never edited.** Each `squads` entry
  applies from its `from` gameweek. Editing an old entry silently
  rescores past weeks against a squad that was never fielded, and the
  table will look plausible while being wrong.
- **Season rollover will invalidate player ids.** `teams.json` and
  `prices.json` key on FPL element ids, which change between seasons.
  A new season needs both rebuilt, and `first_gameweek` reset.
- **The GW3–5 line-ups were nearly lost.** They were recovered from a
  second photograph. There is no other copy. **Record each transfer
  window as it happens** — the date, and the players in *and* out.

## Mistakes already made here, so you don't repeat them

- **Goals must come from exactly one source.** The table once counted
  them from per-player histories while the bots used `/event/{gw}/live`.
  Separate endpoints, separate cache lifetimes: during a live gameweek
  the app disagreed with itself. `standings.goals_by_gameweek` is now the
  only reader and two tests pin that.
- **Cache writes must be atomic.** `open(path, "w")` truncates in place,
  and the page fires three requests at once over several threads, so
  readers hit half-written files. This showed up as the table
  intermittently failing to load with no obvious pattern.
- **Per-manager stats must use the squad held that week.** Crediting a
  manager with their current squad across all weeks hands transferers
  their new picks' earlier goals. The check is that totals equal
  `standings.table()`'s GS.
- **A total can be right for the wrong reason.** Two different theories
  both reproduced the goal totals exactly; only the W/D/L record
  distinguished them. Verify against points and results, not goals alone.
- **Generated prose must compute its comparisons.** A fact asserting
  "more than double any outfield pick" was true over one range and false
  over another.

## Where things live

`standings.py` is the table and the only place goals are read.
`robot.py` holds the two optimisers, `insights.py` everything decorative
but factual, `app.py` the server and asset cache, `index.html` the whole
front end. `tools/check_palette.py` re-checks manager colours for
colour-blind separation after you edit `palette.json`.

`README.md` covers running it and the data formats.

## If you want somewhere to start

The group asked for these and they are not built: a shadow head-to-head
table (pair managers each week for a parallel league), and quantifying
what each manager's locked club pair has cost them. The second one looks
the most interesting: the same seven scorers swing from **0 to 10 points**
purely on which two clubs you own, which is the widest effect found in
the data so far.
