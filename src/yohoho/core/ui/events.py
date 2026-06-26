"""Pure reducer: apply a queue event dict to the PanelModel.

The panel's runner drains a thread-safe queue and calls this from the Tk main
thread — the ONLY place the model is mutated from queued events.
"""

from __future__ import annotations

from yohoho.core.ui.panel_model import PanelModel


def apply_event(model: PanelModel, event: dict) -> None:
    kind = event.get("t")
    if kind == "amp":
        model.push_amplitude_level(event["level"])
    elif kind == "state":
        model.set_state(event["state"])
    elif kind == "terminal":
        model.set_terminal(event["kind"], event.get("code"))
    # unknown event types are ignored
