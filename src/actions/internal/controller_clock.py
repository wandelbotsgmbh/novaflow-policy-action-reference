"""Where the controller's action-chunk session clock is *now*, from delayed reports.

Every waypoint of an action-chunk session is timestamped in ms on a session
clock that the controller starts with the session's first chunk. A waypoint
whose timestamp has already passed when it lands is skipped silently, with no
error and no motion. So the sender has to know that clock, not guess it from
its own elapsed time: a local guess only holds while the network latency is
constant, and once jitter has drifted it past the lead every waypoint looks
already elapsed to the controller.

The controller reports its clock as ``execute.details.jogger_session_timestamp_ms``
on the motion-group state stream, but each report lands after a jittery delay.
So the reports are turned into an offset from the local monotonic clock: a
report can only ever read *earlier* than the true clock at the moment it
lands, so the largest ``reported - received`` over a few seconds is the
tightest estimate, and a delivery stall (which only produces low values)
cannot drag it back. Corrections are slewed in rather than stepped, so a
better estimate never jolts the path.

The constants were measured streaming teleoperation targets over the same
endpoint.
"""

from __future__ import annotations

from collections import deque

CLOCK_WINDOW_S = 5.0
"""Reports older than this drop out of the offset estimate, so it can follow
slow drift between the two clocks."""

CLOCK_RESET_MS = 1000.0
"""A report this far *below* the estimate is a clock that restarted (a new
session): start over from it. One this far *above* is a stale report from
before this session's first chunk."""

CLOCK_SLEW_MS_PER_S = 5.0
"""Corrections are slewed in at this rate (0.5 % time warp at most); only one
bigger than ``CLOCK_JUMP_MS`` is taken at once."""

CLOCK_JUMP_MS = 100.0


class ControllerClock:
    """Offset of the controller's session clock from ``time.monotonic()``.

    The session clock starts with the session's first chunk, and the sender
    says so (:meth:`start`). Before that the state stream still reports the
    *previous* session's last timestamp, frozen (seen on NOVA 26.8: ``execute``
    is already populated before this session sends anything). Taking it for
    the new session's clock stamps every waypoint that far in the future, and
    the arm barely moves. So reports are ignored until :meth:`start`, and
    afterwards one more than ``CLOCK_RESET_MS`` ahead of the estimate is stale.
    """

    def __init__(self) -> None:
        self._reports: deque[tuple[float, float]] = deque()  # (received_s, offset_ms)
        self._provisional_ms: float | None = None
        self._slewed_ms: float | None = None
        self._slewed_at_s = 0.0

    @property
    def started(self) -> bool:
        return self._provisional_ms is not None

    def start(self, local_s: float) -> None:
        """The session's first chunk is about to go out: its clock starts at ~0."""
        self._reports.clear()
        self._provisional_ms = -local_s * 1000.0
        self._slewed_ms = None

    def observe(self, session_ms: int, received_s: float) -> None:
        """Feed one ``jogger_session_timestamp_ms`` report and its local arrival time."""
        current = self.offset_ms
        if current is None:
            return  # no session of ours yet: whatever is reported is an old one
        offset = session_ms - received_s * 1000.0
        if offset > current + CLOCK_RESET_MS:
            return  # a stale report from before this session's first chunk
        if offset < current - CLOCK_RESET_MS:
            self._reports.clear()  # the session restarted under us
        self._reports.append((received_s, offset))
        while self._reports and received_s - self._reports[0][0] > CLOCK_WINDOW_S:
            self._reports.popleft()

    @property
    def offset_ms(self) -> float | None:
        """Controller clock minus local monotonic clock, in ms; None if unknown."""
        if self._reports:
            return max(offset for _, offset in self._reports)
        return self._provisional_ms

    def offset_at(self, local_s: float) -> float | None:
        """:attr:`offset_ms`, but slewed toward rather than jumped to."""
        target = self.offset_ms
        if target is None:
            return None
        if self._slewed_ms is None or abs(target - self._slewed_ms) > CLOCK_JUMP_MS:
            self._slewed_ms = target
        else:
            step = CLOCK_SLEW_MS_PER_S * max(0.0, local_s - self._slewed_at_s)
            self._slewed_ms += max(-step, min(step, target - self._slewed_ms))
        self._slewed_at_s = local_s
        return self._slewed_ms
