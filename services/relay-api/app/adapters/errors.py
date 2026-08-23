from __future__ import annotations


class RetryableProviderError(Exception):
    """A provider failure the bounded worker retry policy may attempt again."""


class TerminalProviderError(Exception):
    """A provider failure that must be audited without another provider retry."""


__all__ = ["RetryableProviderError", "TerminalProviderError"]
