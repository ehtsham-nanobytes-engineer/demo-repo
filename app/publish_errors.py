"""How a publish failed, so a scheduled retry can never post twice.

Every platform client raises `PublishError` with a `kind`. The scheduler reads
it to decide what happens next; publish-now ignores it and shows the message.

- TRANSIENT: nothing reached the feed (the connection failed, the platform
  said 429 or 5xx, or an earlier step such as the image upload failed). Safe
  to try again.
- PERMANENT: the platform refused the post (bad token, missing permission,
  unusable media). Trying again would fail the same way.
- UNKNOWN: the final create call may have landed — it timed out or the
  connection dropped after the request went out. Only a human looking at the
  feed can tell, so this is never retried automatically.

`final=True` marks the call that actually creates the post. Every earlier
step (registering an upload, sending the bytes, making an Instagram
container) leaves nothing on the feed when it fails, however it fails.
"""

from enum import StrEnum

import httpx


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class PublishError(RuntimeError):
    """A publish that did not go through, and whether retrying is safe."""

    def __init__(self, message: str, kind: FailureKind = FailureKind.PERMANENT):
        super().__init__(message)
        self.kind = kind


# Raised before a single byte of the request is sent.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def network_failure(exc: httpx.HTTPError, *, final: bool) -> FailureKind:
    """The kind for an exception raised while talking to a platform."""
    if isinstance(exc, _NOT_SENT) or not final:
        return FailureKind.TRANSIENT
    return FailureKind.UNKNOWN


def status_failure(status: int, *, final: bool) -> FailureKind:
    """The kind for an HTTP error status from a platform."""
    if status == 429:
        return FailureKind.TRANSIENT
    if status >= 500:
        # A gateway timeout on the create call says nothing about whether the
        # platform behind it created the post.
        return FailureKind.UNKNOWN if final and status == 504 else FailureKind.TRANSIENT
    return FailureKind.PERMANENT
