"""The login probe must prove the token, not merely that telemetry is arriving.

Regression test for an authentication bypass: because the airborne daemon streams
telemetry to every return address it has ever accepted a packet from, and the
bridge probed on its long-lived session socket, a *wrong* password was accepted
as soon as any correct login had happened in the bridge's lifetime. The operator
got a cockpit with live telemetry and video but no control authority, since the
aircraft rejected every subsequent command as a bad token.

The fake daemon below reproduces exactly that behaviour: it records senders whose
token it accepts, refuses the rest, and — crucially — keeps streaming telemetry
to everything it has recorded regardless of what it just rejected.
"""
from __future__ import annotations

import asyncio
import json

from app.udp_link import UdpLink

GOOD = "correct-horse"
BAD = "not-the-password"
HOST = "127.0.0.1"


class FakeDaemon(asyncio.DatagramProtocol):
    """Stand-in for airborne_daemon's UDP server."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.recorded: set[tuple[str, int]] = set()
        self.rejected = 0

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            msg = json.loads(data.decode())
        except ValueError:
            return
        if msg.get("token") == GOOD:
            self.recorded.add(addr)
        else:
            self.rejected += 1
        # The daemon's telemetry loop does not care what just got rejected: it
        # keeps publishing to every address it has ever recorded. This is the
        # behaviour that made "telemetry arrived" a worthless password check.
        payload = json.dumps({"t": "tlm", "armed": False}).encode()
        for peer in self.recorded:
            self.transport.sendto(payload, peer)


async def _run_probes():
    loop = asyncio.get_running_loop()
    transport, daemon = await loop.create_datagram_endpoint(
        FakeDaemon, local_addr=(HOST, 0)
    )
    port = transport.get_extra_info("sockname")[1]

    link = UdpLink(port=port, on_telemetry=lambda msg: None, bind_host=HOST)
    await link.start()
    try:
        good_first = await link.probe(HOST, GOOD, timeout=1.0)
        # Now the daemon is streaming to whatever the successful probe used. A
        # wrong password must still be refused.
        bad_after_good = await link.probe(HOST, BAD, timeout=1.0)
        # And a correct one must still work afterwards.
        good_again = await link.probe(HOST, GOOD, timeout=1.0)
        return good_first, bad_after_good, good_again, daemon.rejected
    finally:
        link.stop()
        transport.close()


def test_wrong_password_is_refused_even_after_a_successful_login() -> None:
    good_first, bad_after_good, good_again, rejected = asyncio.run(_run_probes())

    assert good_first is True, "the correct token should authenticate"
    assert bad_after_good is False, (
        "a wrong password was accepted because the daemon was already streaming "
        "telemetry to the probing socket — this is the auth bypass"
    )
    assert good_again is True, "the correct token must still work after a failure"
    assert rejected >= 1, "the fake daemon should have rejected the bad token"


def test_probe_refused_when_nothing_is_listening() -> None:
    async def run():
        link = UdpLink(port=59999, on_telemetry=lambda msg: None, bind_host=HOST)
        await link.start()
        try:
            return await link.probe(HOST, GOOD, timeout=0.4)
        finally:
            link.stop()

    assert asyncio.run(run()) is False
