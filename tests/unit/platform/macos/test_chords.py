import pytest
from yohoho.platform.macos.chords import ChordMatcher, parse_spec, normalize_id


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
