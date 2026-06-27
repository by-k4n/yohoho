import pytest
from yohoho.platform._shared.chords import (
    ChordMatcher,
    parse_spec,
    normalize_id,
    raw_to_token,
    holds_to_spec,
)


def test_parse_spec_to_required_ids():
    assert parse_spec("ctrl+alt+space") == frozenset({"ctrl", "alt", "space"})


def test_normalize_collapses_left_right_modifiers():
    assert normalize_id("ctrl_l") == "ctrl" and normalize_id("ctrl_r") == "ctrl"
    assert normalize_id("alt_r") == "alt" and normalize_id("a") == "a"


def test_holding_both_modifier_variants_then_releasing_one_does_not_refire():
    # cmd_l and cmd_r both normalize to 'cmd'. Holding both, then releasing one
    # while the other (+ shift) is still down, must NOT re-arm and re-fire.
    fired = []
    m = ChordMatcher("cmd+shift", on_activate=lambda: fired.append(1))
    m.press("cmd")        # left cmd (pynput reports 'cmd')
    m.press("cmd_r")      # right cmd
    m.press("shift")
    assert fired == [1]               # fired once when fully down
    m.release("cmd")                  # release left; right cmd + shift still held
    m.press("cmd")                    # press left again
    assert fired == [1]               # chord stayed satisfied -> no spurious second fire


def test_fires_once_on_chord_completion():
    fired = []
    m = ChordMatcher("ctrl+alt+space", on_activate=lambda: fired.append(1))
    m.press("ctrl")
    m.press("alt")
    assert fired == []            # incomplete
    m.press("space")
    assert fired == [1]           # completed -> one activation
    m.press("space")              # re-press while held: no re-fire
    assert fired == [1]


def test_refires_after_release_then_recomplete():
    fired = []
    m = ChordMatcher("ctrl+alt+space", on_activate=lambda: fired.append(1))
    for k in ("ctrl", "alt", "space"):
        m.press(k)
    m.release("space")
    for k in ("space",):
        m.press(k)   # complete again
    assert fired == [1, 1]


def test_left_right_modifier_satisfies_chord():
    fired = []
    m = ChordMatcher("ctrl+alt+space", on_activate=lambda: fired.append(1))
    m.press("ctrl_l")
    m.press("alt_r")
    m.press("space")
    assert fired == [1]


def test_empty_spec_rejected():
    with pytest.raises(ValueError):
        ChordMatcher("", on_activate=lambda: None)


def test_raw_to_token_side_specific():
    assert raw_to_token("cmd_r") == "rcmd"
    assert raw_to_token("alt_gr") == "ralt"
    assert raw_to_token("ctrl_l") == "lctrl"
    assert raw_to_token("space") == "space"        # literal passes through
    assert raw_to_token("cmd") == "cmd"            # side-less -> generic


def test_holds_to_spec_orders_modifiers_first():
    assert holds_to_spec({"ctrl_l", "space"}) == "lctrl+space"
    assert holds_to_spec({"space", "alt_r", "cmd_r"}) == "ralt+rcmd+space"


def test_side_specific_token_matches_only_its_side():
    fired = []
    m = ChordMatcher("rcmd+space", on_activate=lambda: fired.append(1))
    m.press("cmd_l")
    m.press("space")
    assert fired == []                 # left cmd must NOT satisfy 'rcmd'
    m.press("cmd_r")
    assert fired == [1]                # right cmd does


def test_generic_token_still_matches_either_side():
    fired = []
    m = ChordMatcher("cmd+space", on_activate=lambda: fired.append(1))
    m.press("cmd_r")
    m.press("space")
    assert fired == [1]                # generic 'cmd' accepts right cmd


def test_generic_alt_accepts_altgr():
    fired = []
    m = ChordMatcher("alt+space", on_activate=lambda: fired.append(1))
    m.press("alt_gr")
    m.press("space")
    assert fired == [1]


def test_side_specific_release_one_side_rearms_correctly():
    fired = []
    m = ChordMatcher("rcmd+space", on_activate=lambda: fired.append(1))
    m.press("cmd_l")
    m.press("cmd_r")
    m.press("space")
    assert fired == [1]
    m.release("cmd_l")                 # left up; right cmd + space still held
    m.press("cmd_l")
    assert fired == [1]                # stayed satisfied -> no re-fire
    m.release("cmd_r")                 # now 'rcmd' unsatisfied -> re-arm
    m.press("cmd_r")
    assert fired == [1, 1]
