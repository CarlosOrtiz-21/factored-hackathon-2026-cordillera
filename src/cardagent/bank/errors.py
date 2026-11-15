"""Typed errors, each with a stable `error_code` that agents and the audit log can use."""


class BankError(Exception):
    code = "bank_error"


class InvalidSessionError(BankError):
    code = "invalid_session"


class SessionExpiredError(BankError):
    code = "session_expired"


class UnregisteredToolError(BankError):
    code = "unregistered_tool"


class ToolArgsError(BankError):
    code = "invalid_arguments"


class NotFoundError(BankError):
    """Also raised for resources owned by ANOTHER customer: we never confirm they exist."""

    code = "not_found"


class ConfirmationRequiredError(BankError):
    """A write was attempted with no customer confirmation at all."""

    code = "confirmation_required"


class ConfirmationInvalidError(BankError):
    """The confirmation exists but doesn't authorize this write (pending, lapsed, used...)."""

    code = "confirmation_invalid"


class ApprovalRequiredError(BankError):
    """A risky write has the customer's confirmation but still needs a supervisor."""

    code = "approval_required"


class ApprovalInvalidError(BankError):
    code = "approval_invalid"


class ActionNotAllowedError(BankError):
    code = "action_not_allowed"


class IdempotencyConflictError(BankError):
    code = "idempotency_conflict"


class ToolUnavailableError(BankError):
    code = "tool_unavailable"
