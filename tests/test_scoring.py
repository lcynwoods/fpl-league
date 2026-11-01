"""Scoring tests, with the own-goal edge cases pinned down."""
import scoring

RULES = {
    "appearance": {"any_minutes": 1, "sixty_plus": 1},
    "default": {"goals_scored": 4, "assists": 3, "own_goals": -2,
                "yellow_cards": -1},
    "by_position": {"DEF": {"goals_scored": 6, "clean_sheets": 4,
                            "goals_conceded_per_2": -1}},
}


def gw(**kw):
    row = {"round": 1, "minutes": 90, "goals_scored": 0, "assists": 0,
           "own_goals": 0, "clean_sheets": 0, "goals_conceded": 0,
           "yellow_cards": 0}
    row.update(kw)
    return row


def test_goals_and_own_goals_score_independently():
    """An own goal must not cancel a real goal -- both are counted."""
    total, bd = scoring.score_gameweek(gw(goals_scored=1, own_goals=1), "FWD", RULES)
    assert bd["goals_scored"] == 4
    assert bd["own_goals"] == -2
    assert total == 4 - 2 + 1 + 1  # goal, OG, appearance, 60+


def test_own_goal_cancels_the_clean_sheet_but_is_charged_once():
    """A defender who scores an OG concedes: no clean sheet, one OG penalty."""
    total, bd = scoring.score_gameweek(
        gw(own_goals=1, clean_sheets=0, goals_conceded=1), "DEF", RULES)
    assert bd["own_goals"] == -2
    assert "clean_sheets" not in bd
    assert "goals_conceded" not in bd  # 1 // 2 == 0
    assert total == -2 + 1 + 1


def test_multiple_own_goals_in_one_match():
    _, bd = scoring.score_gameweek(gw(own_goals=2), "DEF", RULES)
    assert bd["own_goals"] == -4


def test_position_override_beats_default():
    _, bd = scoring.score_gameweek(gw(goals_scored=1), "DEF", RULES)
    assert bd["goals_scored"] == 6  # DEF override, not the default 4


def test_unused_sub_scores_nothing():
    total, bd = scoring.score_gameweek(gw(minutes=0), "MID", RULES)
    assert total == 0 and bd == {}


def test_season_totals_sum_gameweeks():
    history = [gw(round=1, goals_scored=1), gw(round=2, own_goals=1)]
    total, per_gw = scoring.score_season(history, "FWD", RULES)
    assert per_gw == {1: 6, 2: 0}
    assert total == 6




# --- regression tests: bugs found while waiting on the real rules ---

def test_double_gameweek_accumulates_instead_of_overwriting():
    """Two fixtures in one round must sum, not clobber each other."""
    history = [gw(round=29, goals_scored=1), gw(round=29, goals_scored=2)]
    total, per_gw = scoring.score_season(history, "FWD", RULES)
    # Each fixture is scored in full, appearance points included:
    #   (4 + 1 + 1) + (8 + 1 + 1) == 16
    assert per_gw == {29: 16}, per_gw
    assert total == 16


def test_blank_gameweek_is_absent_not_zero():
    """No fixture means no row, so the round simply isn't in the mapping."""
    _, per_gw = scoring.score_season([gw(round=1), gw(round=3)], "MID", RULES)
    assert set(per_gw) == {1, 3}


def test_unknown_stat_key_is_rejected():
    """A typo'd rule must fail loudly, not silently score nothing."""
    try:
        scoring.validate_rules({"default": {"own_goal": -2}})
    except scoring.RulesError as exc:
        assert "own_goal" in str(exc)
    else:
        raise AssertionError("typo'd stat key was accepted")


def test_synthetic_stats_are_valid_rule_keys():
    scoring.validate_rules({"by_position": {"DEF": {"goals_conceded_per_2": -1}}})


def test_shipped_rules_file_validates():
    scoring.load_rules()


def test_negative_season_total_is_allowed():
    """A bad run of own goals and cards can go below zero."""
    history = [gw(own_goals=2, yellow_cards=1, minutes=90)]
    total, _ = scoring.score_season(history, "FWD", RULES)
    assert total == -4 - 1 + 1 + 1
    assert total < 0


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  ok  %s" % name)
            passed += 1
    print("\n%d tests passed" % passed)
