"""双向心跳。

核心侧与 Windows agent 互相心跳，任一侧失联告警。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import threading
import time
from typing import Callable

import structlog

from qsys.live.messages import Heartbeat
from qsys.live.transport import Transport

log = structlog.get_logger(__name__)


class HeartbeatMonitor:
    def __init__(self, transport: Transport, *, prefix: str = "state:heartbeat") -> None:
        self._t = transport
        self._prefix = prefix

    def _key(self, source: str) -> str:
        return f"{self._prefix}:{source}"

    def beat(
        self, source: str, now: datetime | None = None, *,
        expected_interval_s: float | None = None, phase: str | None = None,
        activity: str | None = None, event_key: str | None = None,
        event_phase: str | None = None, event_started_at: datetime | None = None,
        event_timeout_s: float | None = None, event_timed_out: bool = False,
    ) -> None:
        moment = now or datetime.now()
        self._t.set_key(
            self._key(source), Heartbeat(
                source=source, at=moment, expected_interval_s=expected_interval_s, phase=phase,
                activity=activity, event_key=event_key, event_phase=event_phase,
                event_started_at=event_started_at, event_timeout_s=event_timeout_s,
                event_timed_out=event_timed_out,
            ).model_dump_json()
        )

    def read(self, source: str) -> Heartbeat | None:
        raw = self._t.get_key(self._key(source))
        return None if raw is None else Heartbeat.model_validate_json(raw)

    def last_beat(self, source: str) -> datetime | None:
        heartbeat = self.read(source)
        return heartbeat.at if heartbeat else None

    def is_alive(
        self, source: str, *, max_age_seconds: float = 60.0, now: datetime | None = None
    ) -> bool:
        last = self.last_beat(source)
        if last is None:
            return False
        age = ((now or datetime.now()) - last).total_seconds()
        return age <= max_age_seconds

    def dead_sources(
        self, sources: Sequence[str], *, max_age_seconds: float = 60.0,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        dead = tuple(
            s for s in sources
            if not self.is_alive(s, max_age_seconds=max_age_seconds, now=now)
        )
        if dead:
            log.error("heartbeat.dead_sources", sources=list(dead))
        return dead


class CoreHeartbeatPublisher:
    """Publish core liveness independently from synchronous strategy work.

    The publisher only owns liveness and the current event marker.  The event
    result remains authoritative in ``LiveStore``.  Once an event exceeds its
    SLA, ``on_event_timeout`` is called exactly once while fresh heartbeats keep
    saying that the process is alive but the event has timed out.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        prefix: str = "state:heartbeat",
        interval_s: float = 1.0,
        event_timeout_s: float = 30.0,
        on_event_timeout: Callable[[str], None] | None = None,
        now: Callable[[], datetime] = datetime.now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._monitor = HeartbeatMonitor(transport, prefix=prefix)
        self.interval_s = interval_s
        self.event_timeout_s = event_timeout_s
        self._on_event_timeout = on_event_timeout
        self._now, self._monotonic = now, monotonic
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._event_key: str | None = None
        self._event_phase: str | None = None
        self._event_started_at: datetime | None = None
        self._event_started_monotonic: float | None = None
        self._event_timed_out = False

    def begin_event(self, key: str, phase: str) -> None:
        with self._lock:
            self._event_key = key
            self._event_phase = phase
            self._event_started_at = self._now()
            self._event_started_monotonic = self._monotonic()
            self._event_timed_out = False
        self.pulse_once()

    def end_event(self) -> bool:
        timed_out = self.event_timed_out()
        with self._lock:
            self._event_key = None
            self._event_phase = None
            self._event_started_at = None
            self._event_started_monotonic = None
            self._event_timed_out = False
        self.pulse_once()
        return timed_out

    def event_timed_out(self) -> bool:
        callback_key = None
        with self._lock:
            if self._event_started_monotonic is not None and not self._event_timed_out:
                elapsed = self._monotonic() - self._event_started_monotonic
                if elapsed > self.event_timeout_s:
                    self._event_timed_out = True
                    callback_key = self._event_key
            timed_out = self._event_timed_out
        if callback_key is not None:
            log.error(
                "core_heartbeat.event_timeout", event_key=callback_key,
                timeout_s=self.event_timeout_s,
            )
            if self._on_event_timeout is not None:
                try:
                    self._on_event_timeout(callback_key)
                except Exception as exc:
                    log.error(
                        "core_heartbeat.event_timeout_callback_failed",
                        event_key=callback_key, error=str(exc),
                    )
        return timed_out

    def pulse_once(self) -> None:
        timed_out = self.event_timed_out()
        with self._lock:
            key = self._event_key
            self._monitor.beat(
                "core", self._now(), expected_interval_s=self.interval_s,
                activity="event" if key else "idle",
                event_key=key, event_phase=self._event_phase,
                event_started_at=self._event_started_at,
                event_timeout_s=self.event_timeout_s if key else None,
                event_timed_out=timed_out,
            )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.pulse_once()
        self._thread = threading.Thread(
            target=self._run, name="core-heartbeat", daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.pulse_once()
            except Exception as exc:
                # A transient Redis failure must not permanently kill the
                # publisher; stale-heartbeat safety remains the fallback.
                log.error("core_heartbeat.publish_failed", error=str(exc))

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=max(1.0, self.interval_s * 2))

    def __enter__(self) -> CoreHeartbeatPublisher:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
