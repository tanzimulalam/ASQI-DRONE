"""The aircraft this ground station can fly, and their health.

A fleet is a short list of named drones, each with the address the bridge reaches
it on and, optionally, its session token. It is read from a JSON file kept
outside the repository:

    {
      "drones": [
        {"name": "Piper", "ip": "10.42.0.1",    "token": "..."},
        {"name": "Omega", "ip": "10.130.143.5", "token": "..."}
      ]
    }

The token is used for one thing only: read-only health probes on the fleet
screen, so the operator can see battery, GPS and pre-arm state before logging in.
It is never sent to a browser, and it does not grant control. Flying an aircraft
still requires the operator to type that aircraft's password, which the bridge
validates by probing exactly as it always has. The file holds secrets, so it
belongs at mode 0640 owned by root with the bridge's group.

Without a fleet file the bridge falls back to its candidate IP list, naming the
entries generically and carrying no tokens. Reachability still works; health does
not, because there is nothing to authenticate the probe with.

Two ways of asking "is it there", deliberately kept apart:

* ``jetson_reachable`` opens a TCP connection to the drone Jetson's SSH port. It
  never touches the aircraft's UDP control port, so it is safe to poll every few
  seconds no matter what the aircraft is doing.
* A health probe talks to the airborne daemon itself. The daemon addresses its
  telemetry to whoever last sent it a valid packet, so a probe briefly becomes
  that destination. Harmless on an idle aircraft, but on the aircraft being flown
  it would steal the cockpit's telemetry stream. The caller must never probe the
  active session's drone; its live telemetry is already on hand.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Fields copied from a telemetry packet into a health summary. Everything the
# fleet screen shows comes from this list, so nothing unexpected reaches a browser.
HEALTH_FIELDS = (
    "connected",
    "armed",
    "mode",
    "batt_v",
    "batt_pct",
    "gps_fix",
    "sats",
    "ekf_ok",
    "failsafe",
    "ctrl_age_ms",
    "statustext",
)


@dataclass(frozen=True)
class Drone:
    name: str
    ip: str
    token: str | None = None

    @property
    def key(self) -> str:
        """Case-insensitive identifier used in URLs and login messages."""
        return self.name.lower()


def load_fleet(path: Path | None, fallback_ips: list[str]) -> list[Drone]:
    """Read the fleet file, or build a token-less fleet from the candidate IPs."""
    if path is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            drones = [
                Drone(
                    name=str(d["name"]).strip(),
                    ip=str(d["ip"]).strip(),
                    token=(str(d["token"]) if d.get("token") else None),
                )
                for d in raw.get("drones", [])
            ]
            names = [d.key for d in drones]
            if len(names) != len(set(names)):
                raise ValueError("duplicate drone names in fleet file")
            if drones:
                log.info("fleet: %s", ", ".join(f"{d.name}@{d.ip}" for d in drones))
                return drones
        except FileNotFoundError:
            log.info("no fleet file at %s; using candidate IPs", path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # A broken fleet file must not stop the bridge from flying: fall back
            # to the candidate list and say so loudly.
            log.error("fleet file %s unusable (%s); using candidate IPs", path, exc)

    return [Drone(name=f"Drone {i}", ip=ip) for i, ip in enumerate(fallback_ips, start=1)]


def find(fleet: list[Drone], name: str) -> Drone | None:
    key = name.strip().lower()
    return next((d for d in fleet if d.key == key), None)


async def jetson_reachable(ip: str, port: int = 22, timeout: float = 1.0) -> bool:
    """True if the drone Jetson accepts a TCP connection. Never touches UDP 14650."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


def summarise(telemetry: dict | None) -> dict | None:
    if not telemetry:
        return None
    return {k: telemetry.get(k) for k in HEALTH_FIELDS}
