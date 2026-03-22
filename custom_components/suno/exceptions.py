"""Exceptions for the Suno integration."""


class SunoApiError(Exception):
    """Base exception for Suno API errors."""


class SunoAuthError(SunoApiError):
    """Raised when authentication fails (expired cookie, invalid JWT, etc.)."""


class SunoConnectionError(SunoAuthError):
    """Raised when Suno/Clerk is unreachable (network down, DNS failure, etc.)."""
