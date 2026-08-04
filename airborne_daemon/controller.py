"""Control loop, link watchdog, failsafe state machine, and arm/disarm.

The core decisions are pure functions (:func:`classify_phase`, :func:`decide_action`)
so they can be unit-tested without hardware or threads. :class:`Controller` wires
those decisions to the MAVLink link, runs the fixed-rate control loop, and handles
commands on a dedicated worker so blocking confirmations never stall the loop.

Failsafe policy: brief dropouts (< failsafe threshold) hover on neutral sticks and
recover automatically. Crossing the failsafe threshold while armed *latches*: the
daemon neutralizes, releases the override, and commands the failsafe mode (LAND).
A latched failsafe only clears on explicit operator ``resume`` or on disarm — it
never silently resumes piloting.
"""
from __future__ import annotations

import enum
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import rc
from .modes import mode_name, mode_number
from .protocol import CommandPacket

if TYPE_CHECKING:
    from .mavlink_link import MavlinkLink
    from .settings import Settings
    from .state import ControlState, EventBus, VehicleState

log = logging.getLogger(__name__)


class LinkPhase(enum.Enum):
    NOMINAL = "nominal"
    DEGRADED = "degraded"
    STALE = "stale"
    FAILSAFE = "failsafe"


class ControlAction(enum.Enum):
    SEND_STICKS = enum.auto()
    HOLD_NEUTRAL = enum.auto()
    TRIP_FAILSAFE = enum.auto()
    IDLE = enum.auto()


@dataclass(frozen=True, slots=True)
class WatchdogThresholds:
    degraded_ms: int
    stale_ms: int
    failsafe_ms: int


def classify_phase(age_ms: int, t: WatchdogThresholds) -> LinkPhase:
    """Classify link health from the age of the newest valid control packet."""
    if age_ms >= t.failsafe_ms:
        return LinkPhase.FAILSAFE
    if age_ms >= t.stale_ms:
        return LinkPhase.STALE
    if age_ms >= t.degraded_ms:
        return LinkPhase.DEGRADED
    return LinkPhase.NOMINAL


def decide_action(
    age_ms: int,
    *,
    armed: bool,
    mode_allowed: bool,
    failsafe_latched: bool,
    thresholds: WatchdogThresholds,
    pilot_takeover: bool = False,
) -> ControlAction:
    """Decide what the control loop should do this tick. Pure and total.

    Ordering matters: a lost link while armed trips the failsafe regardless of
    mode, but in a disallowed mode (e.g. Stabilize) we never push stick overrides.
    A latched pilot takeover idles unconditionally — the RC pilot has the aircraft,
    so even a dead GUI link must not command LAND over their head.
    """
    if pilot_takeover:
        return ControlAction.IDLE
    if failsafe_latched:
        return ControlAction.IDLE
    if not armed:
        return ControlAction.IDLE
    phase = classify_phase(age_ms, thresholds)
    if phase is LinkPhase.FAILSAFE:
        return ControlAction.TRIP_FAILSAFE
    if not mode_allowed:
        return ControlAction.IDLE
    if phase is LinkPhase.STALE:
        return ControlAction.HOLD_NEUTRAL
    return ControlAction.SEND_STICKS  # NOMINAL or DEGRADED


class Controller:
    def __init__(
        self,
        settings: "Settings",
        link: "MavlinkLink",
        vehicle: "VehicleState",
        control: "ControlState",
        events: "EventBus",
    ) -> None:
        self._s = settings
        self._link = link
        self._vehicle = vehicle
        self._control = control
        self._events = events
        self._thresholds = WatchdogThresholds(
            settings.ctrl_age_degraded_ms,
            settings.ctrl_age_stale_ms,
            settings.ctrl_age_failsafe_ms,
        )
        self._allowed = frozenset(settings.allowed_pilot_modes)
        self._commands: "queue.Queue[CommandPacket]" = queue.Queue(maxsize=64)
        self._overriding = False  # control-loop thread only (benignly reset by failsafe)
        # RC-pilot takeover: watched mirror channels, per-session baseline, latch
        self._watch_channels = tuple(settings.pilot_takeover_channels)
        self._takeover_baseline: dict[int, int] | None = None  # control-loop thread only
        self._pilot_takeover = False
        self._failsafe_latched = False
        self._latch_lock = threading.Lock()
        self._last_phase = LinkPhase.NOMINAL
        self._prev_armed = False
        # Pending auto-takeoff handoff, watched by the (non-blocking) control loop:
        # (target_alt_m, deadline_monotonic, handoff_mode) or None. Guarded so the
        # command worker and control loop never race on it.
        self._takeoff_lock = threading.Lock()
        self._takeoff: tuple[float, float, str] | None = None

    # -- public API --------------------------------------------------------- #
    @property
    def link_phase(self) -> LinkPhase:
        return self._last_phase

    @property
    def failsafe_latched(self) -> bool:
        with self._latch_lock:
            return self._failsafe_latched

    @property
    def pilot_takeover(self) -> bool:
        with self._latch_lock:
            return self._pilot_takeover

    def submit_command(self, cmd: CommandPacket) -> None:
        """Enqueue a command from the UDP thread; never blocks the caller."""
        try:
            self._commands.put_nowait(cmd)
        except queue.Full:
            log.warning("command queue full, dropping %s", cmd.cmd)

    # -- control loop (own thread) ----------------------------------------- #
    def run_control_loop(self, stop: threading.Event) -> None:
        period = 1.0 / self._s.control_hz
        log.info("control loop started at %.0f Hz", self._s.control_hz)
        next_tick = time.monotonic()
        while not stop.is_set():
            self._tick()
            next_tick += period
            sleep = next_tick - time.monotonic()
            if sleep > 0:
                stop.wait(sleep)
            else:
                next_tick = time.monotonic()  # fell behind; resync without drift spiral
        # leave the vehicle in a clean state
        self._link.release_rc_override()
        log.info("control loop stopped")

    def _tick(self) -> None:
        ctl = self._control.snapshot()
        veh = self._vehicle.snapshot()
        mode = mode_name(veh.mode_num)
        mode_allowed = mode in self._allowed
        self._last_phase = classify_phase(ctl.age_ms, self._thresholds)

        # auto-clear the latches once the vehicle is observed disarmed
        if self._prev_armed and not veh.armed:
            self._clear_latch("vehicle disarmed")
            self._clear_pilot_takeover("vehicle disarmed")
        self._prev_armed = veh.armed

        # advance a pending auto-takeoff (non-blocking; keeps DISARM/RTL responsive)
        self._maybe_handoff_takeoff(veh, mode)

        action = decide_action(
            ctl.age_ms,
            armed=veh.armed,
            mode_allowed=mode_allowed,
            failsafe_latched=self.failsafe_latched,
            thresholds=self._thresholds,
            pilot_takeover=self.pilot_takeover,
        )

        # RC pilot touching the transmitter beats everything except a latched state:
        # release the override right now, before this tick can push more GUI sticks.
        if action in (ControlAction.SEND_STICKS, ControlAction.HOLD_NEUTRAL):
            if self._pilot_touched(veh.rc_in):
                self._trip_pilot_takeover()
                return
        else:
            self._takeover_baseline = None  # next pilot session recaptures

        if action is ControlAction.SEND_STICKS:
            self._link.send_rc_override(*self._pwm(ctl.sticks))
            self._overriding = True
        elif action is ControlAction.HOLD_NEUTRAL:
            self._link.send_rc_override(*self._neutral())
            self._overriding = True
        elif action is ControlAction.TRIP_FAILSAFE:
            self._trip_failsafe(f"control link lost ({ctl.age_ms} ms)")
        elif self._overriding:
            # IDLE after piloting: release immediately so the RC transmitter gets
            # stick authority now, not after the FC's RC_OVERRIDE_TIME expires.
            self._overriding = False
            self._link.release_rc_override()
            if veh.armed and not mode_allowed:
                log.warning("mode %s is RC-pilot controlled; override released", mode)
                self._events.publish("pilot_takeover", f"RC pilot has control ({mode})", mode=mode)
            else:
                log.info("override released (armed=%s, mode=%s)", veh.armed, mode)

    def _pwm(self, sticks: rc.Sticks) -> tuple[int, int, int, int]:
        return rc.sticks_to_pwm(
            sticks,
            self._link.calibration,
            invert=self._s.invert_map,
            throttle_center_hold=self._s.throttle_center_hold,
        )

    def _neutral(self) -> tuple[int, int, int, int]:
        return rc.neutral_pwm(
            self._link.calibration, throttle_center_hold=self._s.throttle_center_hold
        )

    # -- RC-pilot takeover ---------------------------------------------------- #
    def _pilot_touched(self, rc_in: tuple[int, ...]) -> bool:
        """True when a watched mirror channel moved beyond the deadzone.

        The baseline is captured on the first piloting tick of each override
        session, so it works for both spring-centered and ratchet throttle sticks.
        Implausible PWM (no receiver / channel not output) never triggers.
        """
        if not self._watch_channels:
            return False
        values = {
            ch: rc_in[ch - 1]
            for ch in self._watch_channels
            if len(rc_in) >= ch and 800 <= rc_in[ch - 1] <= 2200
        }
        if not values:
            return False
        if self._takeover_baseline is None:
            self._takeover_baseline = values
            return False
        dz = self._s.pilot_takeover_dz_us
        return any(
            abs(pwm - self._takeover_baseline[ch]) > dz
            for ch, pwm in values.items()
            if ch in self._takeover_baseline
        )

    def _trip_pilot_takeover(self) -> None:
        with self._latch_lock:
            if self._pilot_takeover:
                return
            self._pilot_takeover = True
        self._takeover_baseline = None
        self._overriding = False
        self._link.release_rc_override()
        log.warning("transmitter sticks touched; override released, RC pilot has control")
        self._events.publish("pilot_takeover", "RC pilot has control (transmitter touched)")

    def _clear_pilot_takeover(self, reason: str) -> None:
        with self._latch_lock:
            if not self._pilot_takeover:
                return
            self._pilot_takeover = False
        log.warning("pilot takeover cleared: %s", reason)
        self._events.publish("pilot_takeover_clear", reason)

    # -- failsafe ----------------------------------------------------------- #
    def _trip_failsafe(self, reason: str) -> None:
        with self._latch_lock:
            if self._failsafe_latched:
                return
            self._failsafe_latched = True
        self._clear_takeoff("failsafe tripped")
        log.error("FAILSAFE: %s -> commanding %s", reason, self._s.failsafe_action)
        self._events.publish("failsafe", reason, action=self._s.failsafe_action)
        # neutralize briefly so no stale stick is latched, then release + LAND
        neutral = self._neutral()
        for _ in range(5):
            self._link.send_rc_override(*neutral)
            time.sleep(0.02)
        self._link.release_rc_override()
        self._overriding = False
        self._link.set_mode(mode_number(self._s.failsafe_action))

    def _clear_latch(self, reason: str) -> None:
        with self._latch_lock:
            if not self._failsafe_latched:
                return
            self._failsafe_latched = False
        log.warning("failsafe latch cleared: %s", reason)
        self._events.publish("failsafe_clear", reason)

    # -- command worker (own thread) --------------------------------------- #
    def run_command_worker(self, stop: threading.Event) -> None:
        log.info("command worker started")
        while not stop.is_set():
            try:
                cmd = self._commands.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._handle_command(cmd)
            except Exception:  # pragma: no cover - never let the worker die
                log.exception("error handling command %s", cmd)
        log.info("command worker stopped")

    def _handle_command(self, cmd: CommandPacket) -> None:
        if cmd.cmd == "arm":
            self._do_arm()
        elif cmd.cmd == "disarm":
            self._do_disarm()
        elif cmd.cmd == "set_mode":
            self._do_set_mode(cmd.mode)
        elif cmd.cmd == "takeoff":
            self._do_takeoff(cmd.alt)
        elif cmd.cmd == "failsafe":
            self._trip_failsafe("manual failsafe requested from GUI")
        elif cmd.cmd == "resume":
            self._clear_latch("operator resume")
            self._clear_pilot_takeover("operator resume")

    def _do_set_mode(self, mode: str | None) -> None:
        if not mode:
            return
        try:
            num = mode_number(mode)
        except KeyError:
            log.warning("ignoring set_mode for unknown mode %r", mode)
            return
        self._link.set_mode(num)
        self._events.publish("mode_req", f"requested {mode}")

    def _do_arm(self) -> None:
        veh = self._vehicle.snapshot()
        ctl = self._control.snapshot()

        if self._s.arm_require_fresh_control and ctl.age_ms > self._s.ctrl_age_stale_ms:
            return self._reject_arm("no fresh control link")
        if not veh.connected:
            return self._reject_arm("no flight-controller heartbeat")

        if self._s.arm_require_mode_allowed and mode_name(veh.mode_num) not in self._allowed:
            # attempt to move into the default pilot mode, then re-check
            self._link.set_mode(mode_number(self._s.default_pilot_mode))
            time.sleep(1.0)
            veh = self._vehicle.snapshot()
            if mode_name(veh.mode_num) not in self._allowed:
                return self._reject_arm(f"mode {mode_name(veh.mode_num)} not allowed for web piloting")

        if self._s.arm_require_throttle_centered and abs(ctl.sticks.throttle) > self._s.arm_throttle_center_tol:
            return self._reject_arm("throttle not centered")

        self._link.send_arm(True)
        if self._wait_for_armed(True, timeout=4.0):
            self._events.publish("armed", "vehicle ARMED", armed=True)
            log.info("vehicle armed")
        else:
            self._events.publish("arm_fail", "arm not confirmed by vehicle")
            log.warning("arm command not confirmed")

    def _do_disarm(self) -> None:
        self._link.send_arm(False)  # normal disarm; ArduPilot refuses if unsafe
        if self._wait_for_armed(False, timeout=4.0):
            self._events.publish("disarmed", "vehicle DISARMED", armed=False)
            log.info("vehicle disarmed")
        else:
            self._events.publish("disarm_fail", "disarm not confirmed (refused if not landed)")
            log.warning("disarm not confirmed")

    def _do_takeoff(self, alt: float | None) -> None:
        """Arm in GUIDED and command an auto-climb, then arm a pending handoff so
        the control loop hands piloting back in LOITER once altitude is reached.

        The arm phase blocks briefly (on the ground, motors idle); the climb does
        NOT block the command worker, so DISARM/RTL are processed immediately.
        Selecting any mode during the climb, disarming, or a failsafe cancels the
        pending handoff.
        """
        if not self._s.allow_web_takeoff:
            return self._reject("takeoff", "web takeoff disabled by configuration")
        if alt is None or alt <= 0.0:
            return self._reject("takeoff", "invalid takeoff altitude")
        alt = min(float(alt), self._s.takeoff_max_alt_m)

        veh = self._vehicle.snapshot()
        ctl = self._control.snapshot()
        if self._s.arm_require_fresh_control and ctl.age_ms > self._s.ctrl_age_stale_ms:
            return self._reject("takeoff", "no fresh control link")
        if not veh.connected:
            return self._reject("takeoff", "no flight-controller heartbeat")
        if self._s.arm_require_throttle_centered and abs(ctl.sticks.throttle) > self._s.arm_throttle_center_tol:
            return self._reject("takeoff", "throttle not centered")

        # 1) enter GUIDED (auto-takeoff capable) and confirm from HEARTBEAT
        if mode_name(veh.mode_num) != self._s.takeoff_mode:
            self._link.set_mode(mode_number(self._s.takeoff_mode))
            if not self._wait_for_mode(self._s.takeoff_mode, timeout=3.0):
                return self._reject("takeoff", f"could not enter {self._s.takeoff_mode}")

        # 2) arm (if needed) and confirm
        if not self._vehicle.snapshot().armed:
            self._link.send_arm(True)
            if not self._wait_for_armed(True, timeout=4.0):
                self._events.publish("arm_fail", "arm not confirmed by vehicle")
                log.warning("takeoff aborted: arm not confirmed")
                return
            self._events.publish("armed", "vehicle ARMED", armed=True)

        # 3) command the climb, then let the control loop watch for altitude
        self._link.takeoff(alt)
        deadline = time.monotonic() + max(8.0, alt * 3.0)
        with self._takeoff_lock:
            self._takeoff = (alt * self._s.takeoff_reach_frac, deadline, self._s.default_pilot_mode)
        self._events.publish("takeoff", f"takeoff to {alt:.1f} m", alt=alt)
        log.info("takeoff commanded to %.1f m; will hand off to %s", alt, self._s.default_pilot_mode)

    def _maybe_handoff_takeoff(self, veh, mode: str) -> None:
        """Called every control tick. Switches to the pilot mode once the takeoff
        altitude is reached, or abandons the handoff on disarm / mode change / timeout."""
        with self._takeoff_lock:
            pending = self._takeoff
        if pending is None:
            return
        if self.failsafe_latched:
            return self._clear_takeoff("failsafe")
        target, deadline, handoff = pending
        if not veh.armed:
            return self._clear_takeoff("disarmed during takeoff")
        if mode != self._s.takeoff_mode:
            # operator (or the FC) left GUIDED — they have taken over; don't fight it
            return self._clear_takeoff(f"mode changed to {mode} during takeoff")
        if veh.rel_alt_m >= target:
            self._link.set_mode(mode_number(handoff))
            self._events.publish("takeoff_done", f"reached altitude; {handoff} engaged")
            log.info("takeoff complete; handing off to %s", handoff)
            return self._clear_takeoff("reached altitude")
        if time.monotonic() >= deadline:
            self._events.publish("takeoff_timeout", "altitude not reached; holding in GUIDED")
            log.warning("takeoff altitude not confirmed within deadline")
            return self._clear_takeoff("timeout")

    def _clear_takeoff(self, reason: str) -> None:
        with self._takeoff_lock:
            if self._takeoff is None:
                return
            self._takeoff = None
        log.debug("takeoff handoff cleared: %s", reason)

    def _wait_for_mode(self, name: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if mode_name(self._vehicle.snapshot().mode_num) == name:
                return True
            time.sleep(0.05)
        return False

    def _reject(self, kind: str, reason: str) -> None:
        self._events.publish(f"{kind}_reject", reason)
        log.warning("%s rejected: %s", kind, reason)

    def _reject_arm(self, reason: str) -> None:
        self._events.publish("arm_reject", reason)
        log.warning("arm rejected: %s", reason)

    def _wait_for_armed(self, target: bool, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._vehicle.snapshot().armed == target:
                return True
            time.sleep(0.05)
        return False
