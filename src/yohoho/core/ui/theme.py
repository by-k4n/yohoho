"""Panel palette. Tk canvas has no per-item alpha, so translucent colors are
pre-blended to opaque hex over the near-black background."""


def blend(fg, bg, alpha):
    return "#" + "".join(f"{round(f * alpha + b * (1 - alpha)):02x}" for f, b in zip(fg, bg))


_CYAN_RGB = (57, 191, 198)
_BG_RGB = (8, 9, 10)

BG = "#08090a"
CYAN = "#39bfc6"
OFF_DOT = blend(_CYAN_RGB, _BG_RGB, 58 / 255)  # slightly more visible unlit dots
GLOW = blend(_CYAN_RGB, _BG_RGB, 0.25)  # "#143639"
GLOW_OFF = BG
REC_RED = "#ff5454"
MUTED = "#56777a"
TRANSCRIBING = "#357f85"
ERROR_AMBER = "#f5a623"
CANCELLED_FG = "#9aabad"  # a clearly-visible neutral for the "cancelled" acknowledgement
HOT = "#eafdff"  # near-white cyan: the one-frame Drop & Clack impact flash
