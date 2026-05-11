"""Typed exception hierarchy for external dependencies.

Spec authority: finterminal-spec.md §3.4, master-prompt.md §5.1, §5.4.
"""


class ExternalAPIError(Exception):
    """Generic upstream failure (HTTP error, timeout, transport)."""


class HalalTerminalError(ExternalAPIError):
    """Halal Terminal API unreachable or returned an unexpected error.

    Per spec §3.4 and master-prompt §5.3: NO fallback. The AAOIFI gate
    is non-substituable; downstream services must treat this as a
    blocking "ERROR" verdict and refuse new entries.
    """


class HalalTerminalNotCovered(HalalTerminalError):
    """Halal Terminal does not cover the requested symbol (HTTP 404).

    Not a transport error: the API is healthy, the symbol is simply
    out of coverage. Downstream verdict: "NOT_COVERED".
    """
