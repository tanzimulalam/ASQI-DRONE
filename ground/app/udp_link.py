"""Asyncio UDP link to the airborne daemon.

The session socket is unbound to any single remote so it can talk to whichever
candidate drone accepted the login. Incoming telemetry is cached (for health
checks and priming new clients) and handed to a callback (the WebSocket hub's
broadcast).

Login probes do NOT use that socket — see ``UdpLink.probe`` for why using it made
the password check meaningless.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

TelemetryCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, link: "UdpLink") -> None:
        self._link = link

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._link._transport = transport  # noqa: SLF001

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._link._on_datagram(data, addr)  # noqa: SLF001

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        log.debug("UDP error: %s", exc)


class UdpLink:
    def __init__(self, port: int, on_telemetry: TelemetryCallback, bind_host: str = "0.0.0.0") -> None:
        self._port = port
        self._bind_host = bind_host
        self._on_telemetry = on_telemetry
        self._transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest: dict[str, Any] | None = None
        self._latest_monotonic: float = 0.0

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=(self._bind_host, 0)
        )
        log.info("UDP link bound (ephemeral); probes drone port %d", self._port)

    def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def send(self, message: dict[str, Any], addr: tuple[str, int]) -> None:
        if self._transport is None:
            return
        self._transport.sendto(json.dumps(message, separators=(",", ":")).encode("utf-8"), addr)

    async def probe(self, ip: str, token: str, timeout: float) -> bool:
        """Send a token-bearing heartbeat to ``ip`` and await a telemetry reply.

        Returns True only if the drone at ``ip`` accepted this specific token.

        The probe deliberately runs on its own short-lived socket rather than the
        session socket. The daemon records the return address of the latest packet
        whose token it accepts and streams telemetry there. So on the long-lived session
        socket "telemetry arrived" stops meaning "the token was accepted" the
        moment any correct login has happened in this process's lifetime: the
        drone is already streaming to that port, and every later probe sees a
        reply no matter what token it sent. That made the login gate accept any
        password, handing the operator a cockpit with live telemetry and video but
        no control authority at all, because the aircraft then rejected every
        command packet as a bad token.

        A freshly bound port has never been recorded by the daemon, so a reply to
        it can only mean this probe's token was accepted.
        """
        loop = self._loop or asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()

        class _ProbeProtocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                if addr[0] == ip and not fut.done():
                    fut.set_result(True)

            def error_received(self, exc: Exception) -> None:  # pragma: no cover
                log.debug("probe socket error: %s", exc)

        transport, _ = await loop.create_datagram_endpoint(
            _ProbeProtocol, local_addr=(self._bind_host, 0)
        )
        try:
            transport.sendto(
                json.dumps({"t": "hb", "token": token}, separators=(",", ":")).encode("utf-8"),
                (ip, self._port),
            )
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            transport.close()

    async def probe_telemetry(
        self, ip: str, token: str, timeout: float
    ) -> dict[str, Any] | None:
        """Like :meth:`probe`, but return the telemetry the drone replied with.

        Used for the fleet screen's read-only health check. It keeps the property
        that makes :meth:`probe` trustworthy: a freshly bound socket the daemon has
        never seen, so a telemetry reply can only mean this token was accepted.
        :meth:`probe` is left alone because it guards the login gate.

        The daemon addresses telemetry to whoever last sent it a valid packet, so
        this briefly becomes that destination. Never call it on the aircraft of the
        active session; that would steal the cockpit's telemetry mid-flight.
        """
        loop = self._loop or asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()

        class _HealthProtocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                if addr[0] != ip or fut.done():
                    return
                try:
                    msg = json.loads(data)
                except ValueError:
                    return
                if isinstance(msg, dict) and msg.get("t") == "tlm":
                    fut.set_result(msg)

            def error_received(self, exc: Exception) -> None:  # pragma: no cover
                log.debug("health probe socket error: %s", exc)

        transport, _ = await loop.create_datagram_endpoint(
            _HealthProtocol, local_addr=(self._bind_host, 0)
        )
        try:
            transport.sendto(
                json.dumps({"t": "hb", "token": token}, separators=(",", ":")).encode("utf-8"),
                (ip, self._port),
            )
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            transport.close()

    @property
    def latest_telemetry(self) -> dict[str, Any] | None:
        return self._latest

    @property
    def telemetry_age_ms(self) -> int | None:
        if not self._latest_monotonic:
            return None
        return int((time.monotonic() - self._latest_monotonic) * 1000)

    # -- internals ---------------------------------------------------------- #
    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.debug("dropping non-JSON telemetry datagram")
            return
        if not isinstance(msg, dict):
            return
        self._latest = msg
        self._latest_monotonic = time.monotonic()
        result = self._on_telemetry(msg)
        if asyncio.iscoroutine(result) and self._loop is not None:
            self._loop.create_task(result)
