from yohoho.core.ui.theme import BG, CYAN, ERROR_AMBER, GLOW, HOT, OFF_DOT, blend


def test_palette_constants():
    assert BG == "#08090a" and CYAN == "#39bfc6" and ERROR_AMBER == "#f5a623"


def test_offdot_is_cyan_at_58_255_over_bg():
    assert blend((57, 191, 198), (8, 9, 10), 58 / 255) == OFF_DOT


def test_glow_is_cyan_at_quarter_over_bg():
    assert blend((57, 191, 198), (8, 9, 10), 0.25) == GLOW == "#143639"


def test_hot_is_a_bright_near_white_cyan():
    assert HOT.startswith("#") and len(HOT) == 7          # opaque hex
    # brighter than cyan on every channel (it's the impact flash)
    h = tuple(int(HOT[i:i+2], 16) for i in (1, 3, 5))
    c = tuple(int(CYAN[i:i+2], 16) for i in (1, 3, 5))
    assert all(hc >= cc for hc, cc in zip(h, c)) and h != c
