"""Retry delays shared by camera reconnects, task restarts and alert retries."""

import random


class Backoff:
    """Delays of ``initial * multiplier**n`` capped at ``maximum``, each randomised by
    ``±jitter`` so many cameras recovering from the same outage do not retry in lockstep."""

    def __init__(
        self,
        *,
        initial: float = 1.0,
        maximum: float = 30.0,
        multiplier: float = 2.0,
        jitter: float = 0.2,
        rng: random.Random | None = None,
    ) -> None:
        if initial <= 0 or maximum < initial or multiplier < 1 or not 0 <= jitter < 1:
            msg = "invalid backoff parameters"
            raise ValueError(msg)
        self._initial, self._maximum = initial, maximum
        self._multiplier, self._jitter = multiplier, jitter
        self._rng = rng or random.Random()  # noqa: S311 - timing jitter, not cryptography
        self._attempt = 0

    def next_delay(self) -> float:
        base = min(self._initial * self._multiplier**self._attempt, self._maximum)
        self._attempt += 1
        return base * self._rng.uniform(1 - self._jitter, 1 + self._jitter)

    def reset(self) -> None:
        self._attempt = 0
