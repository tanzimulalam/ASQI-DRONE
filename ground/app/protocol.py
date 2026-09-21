"""Validation of browser -> bridge messages.

Defense in depth: the airborne daemon validates everything again, but the bridge
rejects malformed input early and never forwards unvalidated data onto the flight
network. Validated messages are re-emitted as plain dicts with the session token
injected by the caller.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

# commands the GUI may issue (mirrors the airborne daemon's KNOWN_COMMANDS)
Command = Literal["arm", "disarm", "set_mode", "failsafe", "resume", "takeoff"]

_Axis = Annotated[float, Field(ge=-1.0, le=1.0)]
# structural bound on takeoff altitude; the airborne daemon applies the real cap
_Alt = Annotated[float, Field(gt=0.0, le=120.0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ControlIn(_Strict):
    t: Literal["ctrl"]
    seq: int = Field(ge=0)
    ts: int = 0
    roll: _Axis = 0.0
    pitch: _Axis = 0.0
    thr: _Axis = 0.0
    yaw: _Axis = 0.0


class CommandIn(_Strict):
    t: Literal["cmd"]
    cmd: Command
    mode: str | None = None
    alt: _Alt | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "CommandIn":
        if self.cmd == "takeoff" and self.alt is None:
            raise ValueError("takeoff requires 'alt'")
        if self.cmd != "takeoff" and self.alt is not None:
            raise ValueError("'alt' is only valid for takeoff")
        return self


class HeartbeatIn(_Strict):
    t: Literal["hb"]


class AuthIn(_Strict):
    """Browser -> bridge login. Handled by the bridge and NEVER forwarded to the
    drone: the password IS the drone token, which the bridge injects server-side.
    """

    t: Literal["auth"]
    password: str = Field(min_length=1, max_length=256)
    # Which aircraft to log into, by fleet name. Absent, every candidate is probed
    # and whichever accepts the password connects, which is how single-drone
    # setups have always worked and still do.
    drone: str | None = Field(default=None, min_length=1, max_length=64)


ClientMessage = Annotated[
    Union[ControlIn, CommandIn, HeartbeatIn],
    Field(discriminator="t"),
]


class ClientEnvelope(_Strict):
    """Wrapper enabling discriminated-union validation of a raw client message."""

    message: ClientMessage
