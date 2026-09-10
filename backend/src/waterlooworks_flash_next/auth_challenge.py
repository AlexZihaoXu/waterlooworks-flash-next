"""Current refresh challenge, memory only; never included in refresh events."""

import re
import threading
import time
from collections.abc import Callable

from pydantic import BaseModel, Field

from waterlooworks_flash_next.login import AuthDeadline


class AuthChallengeStatus(BaseModel):
    run_id: str | None = None
    approval_required: bool = False
    code: str | None = Field(default=None, repr=False)
    expires_in_seconds: float = 0


class AuthChallenge:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._run_id: str | None = None
        self._deadline = 0.0
        self._code: str | None = None
        self._expiry: threading.Timer | None = None

    def begin(self, run_id: str, deadline: AuthDeadline) -> None:
        with self._lock:
            if self._expiry is not None:
                self._expiry.cancel()
            self._run_id = run_id
            self._deadline = deadline.expires_at
            self._code = None
            self._expiry = threading.Timer(
                max(0, self._deadline - self._clock()), self.clear, args=(run_id,)
            )
            self._expiry.daemon = True
            self._expiry.start()

    def publish(self, run_id: str, code: str) -> None:
        with self._lock:
            if self._clock() >= self._deadline:
                self.clear(run_id)
            elif run_id == self._run_id and re.fullmatch(r"[0-9]{3}", code):
                self._code = code

    def clear(self, run_id: str) -> None:
        with self._lock:
            if run_id == self._run_id:
                if self._expiry is not None:
                    self._expiry.cancel()
                    self._expiry = None
                self._code = None
                self._run_id = None
                self._deadline = 0

    def snapshot(self, current_run: str | None) -> AuthChallengeStatus:
        with self._lock:
            remaining = self._deadline - self._clock()
            if remaining <= 0 and self._run_id is not None:
                self.clear(self._run_id)
            if self._run_id != current_run or self._run_id is None:
                return AuthChallengeStatus()
            return AuthChallengeStatus(
                run_id=self._run_id,
                approval_required=self._code is not None,
                code=self._code,
                expires_in_seconds=max(0, remaining),
            )
