from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    INSERTING = "inserting"
    CANCELLING = "cancelling"


class Terminal(str, Enum):
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class Outcome(str, Enum):
    PASTED = "pasted"
    COPIED = "copied"
    DISCARDED = "discarded"


class ErrorCode(str, Enum):
    PERM = "PERM"
    PASTE = "PASTE"
    MODEL = "MODEL"
    MIC = "MIC"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class TerminalEvent:
    kind: Terminal
    code: ErrorCode | None = None  # set when kind == ERROR
