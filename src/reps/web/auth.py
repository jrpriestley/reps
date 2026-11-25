"""Sign-in: passwords checked against /etc/shadow, with a lockout."""

import ctypes
import ctypes.util
import functools
import hmac
import threading
import time
from pathlib import Path

MAX_FAILURES = 5
LOCKOUT_SECONDS = 600


@functools.cache
def _libcrypt() -> ctypes.CDLL:
    library = ctypes.CDLL(ctypes.util.find_library("crypt") or "libcrypt.so.1")
    library.crypt.restype = ctypes.c_char_p
    library.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    return library


class Shadow:
    """Check passwords against /etc/shadow through the system's libcrypt."""

    def __init__(self, path: Path, users: dict[str, str]) -> None:
        self.path = path
        self.users = users  # the name typed -> the account checked
        self.lock = threading.Lock()  # crypt() is not thread-safe

    def person(self, typed: str) -> str:
        """Return who a typed name means: the person, or their account's name (bo-web is bo)."""
        for person, account in self.users.items():
            if typed in {person, account}:
                return person
        return typed

    def verify(self, user: str, password: str) -> bool:
        account = self.users.get(user)
        if account is None:
            return False
        stored = ""
        for line in self.path.read_text().splitlines():
            name, _, rest = line.partition(":")
            if name == account:
                stored = rest.split(":")[0]
        if not stored.startswith("$"):
            return False  # locked, or no password yet
        with self.lock:
            computed = _libcrypt().crypt(password.encode(), stored.encode())
        return bool(computed) and hmac.compare_digest(computed, stored.encode())


class Lockout:
    """Count failed sign-ins from each address."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.failures: dict[str, list[float]] = {}

    def _recent(self, address: str) -> list[float]:
        cutoff = time.time() - LOCKOUT_SECONDS
        return [stamp for stamp in self.failures.get(address, []) if stamp > cutoff]

    def locked(self, address: str) -> bool:
        with self.lock:
            return len(self._recent(address)) >= MAX_FAILURES

    def record(self, address: str) -> None:
        with self.lock:
            self.failures[address] = [*self._recent(address), time.time()]

    def clear(self, address: str) -> None:
        with self.lock:
            self.failures.pop(address, None)
