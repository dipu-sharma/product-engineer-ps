class RuntimeBaseError(Exception):
    """Base domain exception for runtime errors."""
    pass


class InvalidStateTransitionError(RuntimeBaseError):
    """Raised when attempting an invalid or illegal state transition."""
    pass


class PolicyViolationError(RuntimeBaseError):
    """Raised when input violates safety or execution policy."""
    pass


class TurnCancelledError(RuntimeBaseError):
    """Raised when turn execution is cooperatively cancelled."""
    pass


class TurnTimeoutError(RuntimeBaseError):
    """Raised when turn execution exceeds configured deadline."""
    pass


class ProviderExecutionError(RuntimeBaseError):
    """Raised when model provider encounters an unrecoverable failure."""
    pass
