from yohoho.core.ui.panel_model import PanelModel, column_height, level_from_raw


def test_level_from_raw():
    assert level_from_raw(0.003) == 0.0
    assert round(level_from_raw(0.02), 2) == 0.51
    assert level_from_raw(0.0364) == 1.0  # clamps to 1.0
    assert level_from_raw(0.0) == 0.0  # clamps to 0.0


def test_column_height_maps_level_to_0_8():
    assert column_height(0.0) == 0
    assert column_height(0.5) == 4
    assert column_height(1.0) == 8


def test_amplitude_peak_holds_then_decays():
    m = PanelModel(columns=30)
    m.push_amplitude_level(1.0)  # a loud burst (already a level, not raw)
    trace = []
    for _ in range(4):
        m.tick()  # each tick scrolls + decays the held level x0.5
        trace.append(round(m.current_level, 4))
    assert trace == [0.5, 0.25, 0.125, 0.0625]


def test_waveform_scrolls_into_deque():
    m = PanelModel(columns=4)
    for lv in (1.0, 0.0, 0.0, 0.0):
        m.push_amplitude_level(lv)
        m.tick()
    # peak-hold keeps the decaying tail visible as it scrolls left
    assert m.waveform_levels() == [1.0, 0.5, 0.25, 0.125]


def test_amplitude_sanitises_nan_and_out_of_range():
    m = PanelModel(columns=4)
    m.push_amplitude_level(float("nan"))  # device glitch → treated as silence
    m.tick()
    m.push_amplitude_level(1.5)  # out of range → clamped to 1.0
    m.tick()
    assert all(0 <= h <= 8 for h in m.waveform_heights())  # never crashes / never off-grid
    assert m.waveform_heights() == [0, 8]
