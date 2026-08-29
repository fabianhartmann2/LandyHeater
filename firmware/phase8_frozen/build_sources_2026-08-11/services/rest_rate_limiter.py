"""Bounded per-peer rate policy for the local Phase-8 REST service.

The limiter owns no socket and never sees request bodies, credentials or CSRF
tokens.  The exact bodyless heater-stop route bypasses every quota so traffic
pressure can never prevent a safe Requested-OFF command.
"""

import time as _time


MAX_PEERS = 4
MAX_REQUESTS_PER_WINDOW = 10
REQUEST_WINDOW_MS = 10000
MAX_MUTATIONS_PER_WINDOW = 2
MUTATION_WINDOW_MS = 1000
CONFIG_COOLDOWN_MS = 5000
PEER_STALE_MS = 60000

_MUTATION_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_STOP_METHOD = "POST"
_STOP_PATH = "/api/v1/heater/stop"


def _plain_ticks_diff(newer, older):
    return newer - older


_platform_ticks_diff = getattr(_time, "ticks_diff", _plain_ticks_diff)


class RestRateLimitError(RuntimeError):
    pass


class RestRateLimitExceeded(RestRateLimitError):
    __slots__ = ("retry_after_seconds",)

    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds

    def __str__(self):
        return "REST request rate exceeded"


class RestRateLimitUnavailable(RestRateLimitError):
    pass


class _PeerState:
    __slots__ = (
        "peer",
        "request_window_ms",
        "request_count",
        "mutation_window_ms",
        "mutation_count",
        "last_config_ms",
        "has_config_commit",
        "last_seen_ms",
    )

    def __init__(self, peer, now_ms):
        self.peer = peer
        self.request_window_ms = now_ms
        self.request_count = 0
        self.mutation_window_ms = now_ms
        self.mutation_count = 0
        self.last_config_ms = now_ms
        self.has_config_commit = False
        self.last_seen_ms = now_ms


def _require_tick(value):
    if type(value) is not int:
        raise ValueError("REST rate-limit tick must be an integer")
    return value


def _canonical_peer(value):
    if type(value) is not str or not value or len(value) > 15:
        raise RestRateLimitUnavailable("REST peer identity is unavailable")
    parts = value.split(".")
    if len(parts) != 4:
        raise RestRateLimitUnavailable("REST peer identity is unavailable")
    normalized = []
    for part in parts:
        if not part or len(part) > 3:
            raise RestRateLimitUnavailable("REST peer identity is unavailable")
        for character in part:
            if not "0" <= character <= "9":
                raise RestRateLimitUnavailable(
                    "REST peer identity is unavailable"
                )
        if len(part) > 1 and part[0] == "0":
            raise RestRateLimitUnavailable("REST peer identity is unavailable")
        number = int(part)
        if number > 255:
            raise RestRateLimitUnavailable("REST peer identity is unavailable")
        normalized.append(str(number))
    canonical = ".".join(normalized)
    if canonical == "0.0.0.0" or canonical == "255.255.255.255":
        raise RestRateLimitUnavailable("REST peer identity is unavailable")
    return canonical


def _is_config_mutation(method, path):
    if method not in _MUTATION_METHODS:
        return False
    return path == "/api/v1/settings" or path == "/api/v1/timers" or path.startswith(
        "/api/v1/timers/"
    )


def _retry_seconds(remaining_ms):
    if remaining_ms <= 0:
        return 1
    value = (remaining_ms + 999) // 1000
    if value < 1:
        return 1
    if value > 60:
        return 60
    return value


class RestRateLimiter:
    """Fixed-table rate limiter using caller-sampled wrap-safe ticks."""

    __slots__ = (
        "__ticks_diff",
        "__peers",
        "__operation_active",
        "__operation_reentered",
        "__faulted",
        "__last_error",
        "__allowed",
        "__rejected",
        "__stop_bypasses",
        "__config_commits",
    )

    def __init__(self, ticks_diff=None):
        if ticks_diff is None:
            ticks_diff = _platform_ticks_diff
        if not callable(ticks_diff):
            raise ValueError("ticks_diff must be callable")
        self.__ticks_diff = ticks_diff
        self.__peers = []
        self.__operation_active = False
        self.__operation_reentered = False
        self.__faulted = False
        self.__last_error = None
        self.__allowed = 0
        self.__rejected = 0
        self.__stop_bypasses = 0
        self.__config_commits = 0

    @property
    def faulted(self):
        return self.__faulted

    def _begin(self):
        if self.__operation_active:
            self.__operation_reentered = True
            raise RestRateLimitUnavailable("REST rate limiter was re-entered")
        if self.__faulted:
            raise RestRateLimitUnavailable("REST rate limiter is faulted")
        self.__operation_active = True
        self.__operation_reentered = False

    def _finish(self):
        reentered = self.__operation_reentered
        self.__operation_active = False
        self.__operation_reentered = False
        if reentered:
            self.__faulted = True
            self.__last_error = "rest_rate_limit_reentrancy_detected"
            raise RestRateLimitUnavailable("REST rate limiter was re-entered")

    def _difference(self, newer, older):
        value = self.__ticks_diff(newer, older)
        if type(value) is not int or value < 0:
            self.__faulted = True
            self.__last_error = "rest_rate_limit_clock_failed"
            raise RestRateLimitUnavailable("REST rate-limit clock is unavailable")
        return value

    def _state(self, peer, now_ms):
        for item in self.__peers:
            if item.peer == peer:
                return item
        if len(self.__peers) < MAX_PEERS:
            item = _PeerState(peer, now_ms)
            self.__peers.append(item)
            return item

        replacement = None
        replacement_age = -1
        for item in self.__peers:
            age = self._difference(now_ms, item.last_seen_ms)
            if age >= PEER_STALE_MS and age > replacement_age:
                replacement = item
                replacement_age = age
        if replacement is None:
            raise RestRateLimitExceeded(1)
        replacement.__init__(peer, now_ms)
        return replacement

    def _consume_window(self, state, now_ms, mutation):
        if mutation:
            started = state.mutation_window_ms
            count = state.mutation_count
            duration = MUTATION_WINDOW_MS
            maximum = MAX_MUTATIONS_PER_WINDOW
        else:
            started = state.request_window_ms
            count = state.request_count
            duration = REQUEST_WINDOW_MS
            maximum = MAX_REQUESTS_PER_WINDOW
        elapsed = self._difference(now_ms, started)
        if elapsed >= duration:
            started = now_ms
            count = 0
            elapsed = 0
        if count >= maximum:
            raise RestRateLimitExceeded(
                _retry_seconds(duration - elapsed)
            )
        count += 1
        if mutation:
            state.mutation_window_ms = started
            state.mutation_count = count
        else:
            state.request_window_ms = started
            state.request_count = count

    def authorize(self, peer, method, path, now_ms):
        """Consume request quotas and return an opaque completion ticket."""

        if type(method) is not str or type(path) is not str:
            raise RestRateLimitUnavailable(
                "REST request identity is unavailable"
            )
        if method == _STOP_METHOD and path == _STOP_PATH:
            try:
                if self.__stop_bypasses < 255:
                    self.__stop_bypasses += 1
            except MemoryError:
                # Diagnostic accounting must never block Requested OFF.
                pass
            return None
        self._begin()
        primary_error = None
        try:
            peer = _canonical_peer(peer)
            now_ms = _require_tick(now_ms)
            state = self._state(peer, now_ms)
            self._consume_window(state, now_ms, False)
            mutation = method in _MUTATION_METHODS
            if mutation:
                self._consume_window(state, now_ms, True)
            config_mutation = _is_config_mutation(method, path)
            if config_mutation and state.has_config_commit:
                elapsed = self._difference(now_ms, state.last_config_ms)
                if elapsed < CONFIG_COOLDOWN_MS:
                    raise RestRateLimitExceeded(
                        _retry_seconds(CONFIG_COOLDOWN_MS - elapsed)
                    )
            state.last_seen_ms = now_ms
            self.__allowed += 1
            return (peer, config_mutation, now_ms)
        except RestRateLimitExceeded as error:
            primary_error = error
            self.__rejected += 1
            raise
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                self._finish()
            except BaseException:
                if primary_error is None:
                    raise

    def complete(self, ticket, config_committed, completed_at_ms=None):
        """Record one successful durable config change after the response exists."""

        if ticket is None:
            return None
        if (
            type(ticket) is not tuple
            or len(ticket) != 3
            or type(ticket[0]) is not str
            or type(ticket[1]) is not bool
            or type(ticket[2]) is not int
            or type(config_committed) is not bool
        ):
            self.__faulted = True
            self.__last_error = "rest_rate_limit_ticket_failed"
            raise RestRateLimitUnavailable("REST rate-limit ticket is invalid")
        if not ticket[1] or not config_committed:
            return None
        completed_at_ms = _require_tick(completed_at_ms)
        self._begin()
        primary_error = None
        try:
            state = None
            for item in self.__peers:
                if item.peer == ticket[0]:
                    state = item
                    break
            if state is None:
                raise RestRateLimitUnavailable(
                    "REST rate-limit peer was lost"
                )
            self._difference(completed_at_ms, ticket[2])
            state.last_config_ms = completed_at_ms
            state.has_config_commit = True
            self.__config_commits += 1
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                self._finish()
            except BaseException:
                if primary_error is None:
                    raise
        return None

    def snapshot(self):
        return {
            "faulted": self.__faulted,
            "last_error": self.__last_error,
            "peer_count": len(self.__peers),
            "allowed": self.__allowed,
            "rejected": self.__rejected,
            "stop_bypasses": self.__stop_bypasses,
            "config_commits": self.__config_commits,
        }
