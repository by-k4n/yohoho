"""Tk Canvas dot-matrix status panel — the brand centerpiece.

A small single-row 280×40 pill that appears already recording (no opening
wordmark).  The brand wordmark ("yohoho" in Doto Regular 400) is shown only at
the end, as the Drop & Clack mechanical reveal: it drops in from above, bounces,
and settles.  Error outcomes animate as an amber message that crawls across the
pill right→left; cancelled outcomes blink the word "cancelled" twice before the
panel hides.

Design constraints baked in here:
  * Tk canvas has NO per-item alpha, so every translucent color (glow, faint
    border) is a PRE-BLENDED opaque hex from ``yohoho.core.ui.theme``.
  * Every canvas item is created ONCE in ``_build()``; ``render()`` only mutates
    them via ``itemconfigure``/``coords`` (never delete + recreate per frame).
  * Font objects are kept alive on ``self`` (Tk garbage-collects unreferenced
    Font objects, which silently breaks the wordmark).

``render()`` performs only Tk drawing and must be called from the Tk main thread
(the runner's concern, not this view's). The exact pixel layout is refined by
eye later (manual gate G2); this module's job is a correct, renderable structure
that maps the model to the canvas.
"""

from __future__ import annotations

import tkinter
import tkinter.font
from typing import Optional

from yohoho.core.ui import theme
from yohoho.core.ui.fonts import PANEL_FONT_ASSET, install_font, resolve_family
from yohoho.core.ui.panel_model import (
    PanelModel,
    cancelled_blink,
    close_step,
    mmss,
    rec_on,
)
from yohoho.core.events import Terminal
from yohoho.core.platform_api import WindowChrome, NullWindowChrome

# Panel size (single-row pill).
_WIDTH = 280
_HEIGHT = 40
_RADIUS = 20            # fully-rounded stadium (= height/2)

# REC dot (left), prominent.
_REC_DOT_CX = 18
_REC_DOT_CY = 20
_REC_R = 4.0
_REC_GLOW_R = 7.0

# Waveform: small dense round dots on an integer pitch. 44 cols x 7 rows.
_COLUMNS = 44
_ROWS = 7
_COL_PITCH = 4          # x: 40, 44, ... 212
_ROW_PITCH = 4          # y: 32, 28, ... 8  (center row at 20)
_GRID_X0 = 40
_GRID_Y_BOTTOM = 32

# Inline timer / percent (right).
_RIGHT_X = 262
_MID_Y = 20

# Usable pill width for the banner (for width-based fallback to short code).
_BANNER_MAX_W = 248

# Wordmark (close only), centered.
_WORD_CX = 140
_WORD_CY = 20

# Dot radii.
_LIT_R = 1.0
_UNLIT_R = 1.0
_GLOW_R = 1.5           # tiny halo (no merge)

# Seconds-per-frame used to derive the elapsed timer from the model frame count
# (~55 ms production tick → ~0.055 s).
_SEC_PER_FRAME = 0.055

# Pre-blended REC glow (red @ 25% over bg) — a constant, not recomputed per frame.
_REC_GLOW = theme.blend((255, 84, 84), (8, 9, 10), 0.25)


def _stadium(c, x0, y0, x1, y1, r, fill):
    """Draw a filled rounded rect (stadium when r == height/2) as 2 ovals + a rect."""
    c.create_oval(x0, y0, x0 + 2 * r, y1, fill=fill, outline="")
    c.create_oval(x1 - 2 * r, y0, x1, y1, fill=fill, outline="")
    c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline="")


class StatusPanel:
    """Always-on-top dot-matrix status window driven by a :class:`PanelModel`."""

    def __init__(
        self, root: tkinter.Tk, model: PanelModel, *, width: int = _WIDTH, height: int = _HEIGHT, window_chrome: Optional[WindowChrome] = None
    ) -> None:
        self.root = root
        self.model = model
        self.width = width
        self.height = height
        self._window_chrome = window_chrome or NullWindowChrome()
        self._build()
        # Start hidden: _build leaves the window MAPPED but parked off-screen.
        # We never withdraw()/deiconify() — on macOS deiconify activates the app,
        # which steals key focus so the synthetic paste lands on us, not the user's
        # app. See the show/hide note in _build.

    # ------------------------------------------------------------------
    # Construction (one-time)
    # ------------------------------------------------------------------

    def _build(self) -> None:
        w, h = self.width, self.height

        self.top = tkinter.Toplevel(self.root)
        self.canvas = tkinter.Canvas(self.top, width=w, height=h, bg=theme.BG, highlightthickness=0)
        self.canvas.pack()

        # --- Fonts ---------------------------------------------------------
        # Install the bundled Doto so Tk can resolve it by family name; fall
        # back gracefully (Menlo, then any available) if it isn't registered
        # with this interpreter yet.
        install_font(PANEL_FONT_ASSET)
        family = resolve_family(sorted(tkinter.font.families(self.root)))
        # Keep references alive on self — Tk GCs unreferenced Font objects.
        self.word_font = tkinter.font.Font(family=family, size=18)
        self.small_font = tkinter.font.Font(family=family, size=11)

        c = self.canvas

        # --- Stadium base card (bottom z-order) ---------------------------
        # A faint 1px border stadium, then bg stadium inset 1px for the ring.
        border = theme.blend((57, 191, 198), (8, 9, 10), 30 / 255)
        _stadium(c, 0, 0, w, h, _RADIUS, border)
        _stadium(c, 1, 1, w - 1, h - 1, _RADIUS - 1, theme.BG)

        # --- Wordmark (close only, hidden by default) ---------------------
        # Revealed only during the Drop & Clack terminal animation (DONE).
        # Created hidden so it is NEVER visible during recording/transcribing.
        self.word_id = c.create_text(
            _WORD_CX, _WORD_CY, anchor="center", text="yohoho",
            font=self.word_font, fill=theme.CYAN, state="hidden",
        )

        # --- Status label (hidden; kept for structural compatibility) ------
        # The "REC"/"transcribing…" text labels are removed from the design.
        # The red dot conveys recording; the progress fill conveys transcribing.
        # The canvas item is preserved to minimise churn but is never shown.
        self.status_id = c.create_text(
            w - 13, _MID_Y, anchor="e", text="", font=self.small_font,
            fill=theme.MUTED, state="hidden",
        )

        # --- REC dot + glow -----------------------------------------------
        rec_cx = _REC_DOT_CX
        rec_cy = _REC_DOT_CY
        self.rec_glow_id = c.create_oval(
            rec_cx - _REC_GLOW_R,
            rec_cy - _REC_GLOW_R,
            rec_cx + _REC_GLOW_R,
            rec_cy + _REC_GLOW_R,
            fill=theme.BG,
            outline="",
            state="hidden",
        )
        self.rec_dot_id = c.create_oval(
            rec_cx - _REC_R,
            rec_cy - _REC_R,
            rec_cx + _REC_R,
            rec_cy + _REC_R,
            fill=theme.BG,
            outline="",
            state="hidden",
        )

        # --- Waveform grid: 44 columns x 7 rows ---------------------------
        self._col_x = [_GRID_X0 + col * _COL_PITCH for col in range(_COLUMNS)]
        self._row_y = [_GRID_Y_BOTTOM - row * _ROW_PITCH for row in range(_ROWS)]

        self.grid: list[list[tuple[int, int]]] = []
        for col in range(_COLUMNS):
            cx = self._col_x[col]
            column_ids: list[tuple[int, int]] = []
            for row in range(_ROWS):
                cy = self._row_y[row]
                glow_id = c.create_oval(
                    cx - _GLOW_R,
                    cy - _GLOW_R,
                    cx + _GLOW_R,
                    cy + _GLOW_R,
                    fill=theme.GLOW_OFF,
                    outline="",
                )
                dot_id = c.create_oval(
                    cx - _UNLIT_R,
                    cy - _UNLIT_R,
                    cx + _UNLIT_R,
                    cy + _UNLIT_R,
                    fill=theme.OFF_DOT,
                    outline="",
                )
                column_ids.append((glow_id, dot_id))
            self.grid.append(column_ids)

        # --- Progress row (single row of 30 dots, hidden until transcribing)
        # Placed at the vertical center of the waveform band so it overlays the
        # dimmed grid cleanly.
        prog_y = _MID_Y
        self._prog_y = prog_y
        self.prog_ids: list[int] = []
        for col in range(_COLUMNS):
            cx = self._col_x[col]
            pid = c.create_oval(
                cx - _LIT_R,
                prog_y - _LIT_R,
                cx + _LIT_R,
                prog_y + _LIT_R,
                fill=theme.OFF_DOT,
                outline="",
                state="hidden",
            )
            self.prog_ids.append(pid)

        # --- Percentage text (right of the progress row) ------------------
        self.pct_id = c.create_text(
            _RIGHT_X,
            _MID_Y,
            anchor="e",
            text="",
            font=self.small_font,
            fill=theme.CYAN,
            state="hidden",
        )

        # --- Timer (right, inline with the single row) --------------------
        self.timer_id = c.create_text(
            _RIGHT_X, _MID_Y, anchor="e", text="00:00", font=self.small_font, fill=theme.MUTED
        )

        # --- Terminal banner (error / cancelled) --------------------------
        # For ERROR: centered short message with blink-twice-then-hold.
        # For CANCELLED: a centered "cancelled" toggled by cancelled_blink.
        self.banner_font = tkinter.font.Font(family=family, size=13)
        self.banner_id = c.create_text(
            w // 2,
            _MID_Y,
            anchor="center",
            text="",
            font=self.banner_font,
            fill=theme.ERROR_AMBER,
            state="hidden",
        )

        # --- Chrome (platform-specific, via the WindowChrome seam) --------
        self._window_chrome.style_window(self.root, self.top, self.canvas)

        # --- Show/hide geometries (visibility = move, never withdraw) ------
        # On macOS, withdraw()/deiconify() ACTIVATE the app, stealing key focus
        # from the user's app so the synthetic Cmd+V paste lands on us instead of
        # them (the M3 dictation bug). So we map the window ONCE here, off-screen,
        # and toggle visibility by sliding it on/off screen — which never activates.
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        on_x = (sw - w) // 2
        on_y = sh - h - 64                       # bottom-center, 64px margin
        self._on_geom = f"{w}x{h}+{on_x}+{on_y}"
        # Far past the PRIMARY's bottom-right corner — off-screen on any single
        # monitor and on virtually all multi-monitor layouts: a side monitor shares
        # the primary's y-range (so this y is below it) and a stacked monitor shares
        # its x-range (so this x is right of it). Only a monitor placed diagonally
        # below-and-right would catch it — far rarer than the above/left arrangement
        # a negative offset is vulnerable to.
        self._off_geom = f"{w}x{h}+{sw + 2000}+{sh + 2000}"
        self.top.geometry(self._off_geom)        # start parked off-screen

    # ------------------------------------------------------------------
    # Per-frame render (Tk main thread only)
    # ------------------------------------------------------------------

    def _clear_interface(self) -> None:
        """Hide every interface element EXCEPT the banner (callers manage the banner).

        Used by the ERROR, CANCELLED, and DONE-close render branches to clear
        the recording/transcribing UI before drawing their own content.
        """
        c = self.canvas
        c.itemconfigure(self.rec_dot_id, state="hidden")
        c.itemconfigure(self.rec_glow_id, state="hidden")
        c.itemconfigure(self.timer_id, state="hidden")
        c.itemconfigure(self.pct_id, state="hidden")
        c.itemconfigure(self.status_id, text="")
        c.itemconfigure(self.word_id, state="hidden")
        for col in range(_COLUMNS):
            for glow_id, dot_id in self.grid[col]:
                c.itemconfigure(dot_id, state="hidden")
                c.itemconfigure(glow_id, state="hidden")
        for pid in self.prog_ids:
            c.itemconfigure(pid, state="hidden")

    def render(self) -> None:
        model = self.model
        c = self.canvas

        # ------------------------------------------------------------------
        # Branch 1: ERROR → centered message, blink-twice-then-hold (readable).
        # ------------------------------------------------------------------
        if model.terminal is Terminal.ERROR:
            visible, done = cancelled_blink(model.frames_since_terminal)
            msg, color = model.banner()
            if self.banner_font.measure(msg) > _BANNER_MAX_W and model.error_code:
                msg = model.error_code
            c.itemconfigure(
                self.banner_id,
                state="normal" if (visible or done) else "hidden",  # blink twice, then stay lit
                text=msg, fill=color, anchor="center",
            )
            c.coords(self.banner_id, self.width // 2, _MID_Y)
            self._clear_interface()
            return

        # ------------------------------------------------------------------
        # Branch 2: CANCELLED → "cancelled" blinks twice then hides.
        # ------------------------------------------------------------------
        if model.terminal is Terminal.CANCELLED:
            visible, _done = cancelled_blink(model.frames_since_terminal)
            c.itemconfigure(
                self.banner_id,
                state="normal" if visible else "hidden",
                text="cancelled",
                fill=theme.CANCELLED_FG,
                anchor="center",
            )
            c.coords(self.banner_id, self.width // 2, _MID_Y)
            self._clear_interface()
            return

        # ------------------------------------------------------------------
        # Branch 3: DONE + close_index >= 0 → Drop & Clack wordmark reveal.
        # During the finish window (close_index < 0) the progress bar is
        # still filling to 100%; we fall through to the normal path below.
        # ------------------------------------------------------------------
        if model.terminal is Terminal.DONE and model.close_index >= 0:
            dy, impact = close_step(model.close_index)
            self._clear_interface()                      # hides rec/wave/progress/pct/timer/status/word
            c.itemconfigure(self.banner_id, state="hidden")
            c.itemconfigure(
                self.word_id, state="normal", anchor="center",
                fill=theme.HOT if impact else theme.CYAN,
            )
            c.coords(self.word_id, _WORD_CX, _WORD_CY + dy)
            c.tag_raise(self.word_id)
            return

        # ------------------------------------------------------------------
        # Normal path: recording / transcribing / DONE finish-window.
        # The wordmark is NEVER shown here.
        # ------------------------------------------------------------------
        c.itemconfigure(self.banner_id, state="hidden")
        c.itemconfigure(self.word_id, state="hidden")

        style = model.style()
        label = style.label
        is_recording = label == "REC"
        # Progress row is shown while transcribing OR during the DONE finish window.
        show_progress = not is_recording and (
            label == "transcribing…" or model.terminal is Terminal.DONE
        )

        # --- Waveform ------------------------------------------------------
        # Dim the grid while the progress row is on top (transcribing/finishing).
        lit_fill = theme.OFF_DOT if show_progress else theme.CYAN
        lit_glow = theme.GLOW_OFF if show_progress else theme.GLOW
        # Right-align: the newest sample sits at the RIGHT edge and the waveform
        # scrolls left; before the deque fills, the empty columns are on the left.
        heights = model.waveform_heights()
        pad = _COLUMNS - len(heights)
        for col in range(_COLUMNS):
            cx = self._col_x[col]
            idx = col - pad
            height = heights[idx] if 0 <= idx < len(heights) else 0
            for row in range(_ROWS):
                glow_id, dot_id = self.grid[col][row]
                cy = self._row_y[row]
                lit = row < height
                if lit:
                    c.itemconfigure(dot_id, state="normal", fill=lit_fill)
                    c.itemconfigure(glow_id, state="normal", fill=lit_glow)
                    c.coords(dot_id, cx - _LIT_R, cy - _LIT_R, cx + _LIT_R, cy + _LIT_R)
                else:
                    c.itemconfigure(dot_id, state="normal", fill=theme.OFF_DOT)
                    c.itemconfigure(glow_id, state="normal", fill=theme.GLOW_OFF)
                    c.coords(dot_id, cx - _UNLIT_R, cy - _UNLIT_R, cx + _UNLIT_R, cy + _UNLIT_R)

        # --- REC dot -------------------------------------------------------
        # Both the dot AND its glow are hidden when not recording — a bg-filled
        # glow disc would otherwise occlude whatever is behind it.
        if is_recording and rec_on(model.frame):
            c.itemconfigure(self.rec_dot_id, state="normal", fill=theme.REC_RED)
            c.itemconfigure(self.rec_glow_id, state="normal", fill=_REC_GLOW)
        else:
            c.itemconfigure(self.rec_dot_id, state="hidden")
            c.itemconfigure(self.rec_glow_id, state="hidden")

        # --- Progress row + percentage ------------------------------------
        if show_progress:
            filled = round(_COLUMNS * model.progress)
            for col in range(_COLUMNS):
                pid = self.prog_ids[col]
                c.itemconfigure(
                    pid,
                    state="normal",
                    fill=theme.CYAN if col < filled else theme.OFF_DOT,
                )
            c.itemconfigure(
                self.pct_id,
                state="normal",
                text=f"{model.progress_pct}%",
                fill=style.accent,
            )
        else:
            for pid in self.prog_ids:
                c.itemconfigure(pid, state="hidden")
            c.itemconfigure(self.pct_id, state="hidden")

        # --- Timer (right-anchored, shown only while recording) ---------------
        # Timer and percentage are MUTUALLY EXCLUSIVE: the percent is shown
        # during transcribing/finish-window (show_progress=True); the timer is
        # shown only while recording (show_progress=False).
        if show_progress:
            c.itemconfigure(self.timer_id, state="hidden")
        else:
            c.itemconfigure(
                self.timer_id,
                state="normal",
                text=mmss(round(model.frame * _SEC_PER_FRAME)),
            )

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def show(self) -> None:
        # Slide on-screen (no deiconify — that would activate the app). topmost is
        # re-asserted so the pill floats over the focused app.
        self.top.geometry(self._on_geom)
        self.top.attributes("-topmost", True)

    def hide(self) -> None:
        # Slide off-screen rather than withdraw() (which would activate on re-show).
        self.top.geometry(self._off_geom)
