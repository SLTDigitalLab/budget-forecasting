"""Backend-enforced lock shared by forecasts, historical edits, and retraining."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from app.services.retrain_state import (
    begin_operation,
    end_operation,
    mutate,
    sweep,
    touch_operation,
    utcnow,
)


def _prepare(state, now=None):
    from app.services.retrain_jobs import candidate_exists, publication_matches, safe_unlink

    sweep(state, now or utcnow(), publication_matches, candidate_exists, safe_unlink)


@contextmanager
def mutation_scope(kind: str):
    """Count this conflicting operation, or reject it while retraining owns the lock."""
    now = utcnow()

    def acquire(state):
        _prepare(state, now)
        return begin_operation(state, kind, now)

    operation_id = mutate(acquire)
    stop = threading.Event()

    def beat():
        while not stop.wait(10):
            def touch(state):
                touch_operation(state, operation_id, utcnow())

            try:
                mutate(touch)
            except Exception:
                return

    threading.Thread(target=beat, name=f"operation-{operation_id[:8]}", daemon=True).start()
    try:
        yield
    finally:
        stop.set()

        def release(state):
            end_operation(state, operation_id, utcnow())

        mutate(release)
