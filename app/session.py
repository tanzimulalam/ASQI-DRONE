"""Active drone session established at login.

The bridge holds no drone connection until a browser authenticates. A successful
login pins the token (the password the operator typed) and the drone address that
accepted it; the keepalive and uplink then use these. Cleared when the last client
disconnects, so an authenticated link never lingers with nobody watching.
"""
from __future__ import annotations


class Session:
    def __init__(self) -> None:
        self.token: str | None = None
        self.remote: tuple[str, int] | None = None

    @property
    def active(self) -> bool:
        return self.token is not None and self.remote is not None

    @property
    def remote_ip(self) -> str | None:
        return self.remote[0] if self.remote else None

    def set(self, token: str, remote: tuple[str, int]) -> None:
        self.token = token
        self.remote = remote

    def clear(self) -> None:
        self.token = None
        self.remote = None
