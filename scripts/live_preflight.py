"""Compatibility wrapper for the packaged live/shadow preflight."""

from qsys.live.preflight_cli import agent_live_posture, heartbeat_freshness, main

__all__ = ["agent_live_posture", "heartbeat_freshness", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
