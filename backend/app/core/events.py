"""In-memory run event bus for SSE. Late subscribers get the recent history replayed.
ponytail: not persisted; after a restart the SSE snapshot (from the DB) is the source of truth."""
import asyncio
import time

_history: dict[str, list[dict]] = {}
_subs: dict[str, set[asyncio.Queue]] = {}
FINAL = ("done", "paused", "stopped", "failed")


def publish(run_id: str, ev: dict) -> None:
    ev = {**ev, "ts": time.time()}
    h = _history.setdefault(run_id, [])
    h.append(ev)
    del h[:-500]
    for q in _subs.get(run_id, ()):
        q.put_nowait(ev)


def subscribe(run_id: str, replay: bool = True) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    if replay:
        for ev in _history.get(run_id, []):
            q.put_nowait(ev)
    _subs.setdefault(run_id, set()).add(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    _subs.get(run_id, set()).discard(q)


def reset(run_id: str) -> None:
    """A (re)started execution begins a fresh history, so subscribers never replay a previous pause/done event."""
    _history.pop(run_id, None)
