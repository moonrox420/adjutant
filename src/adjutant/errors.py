class DomainError(Exception):
    """A safe, actionable error that may be returned to the caller."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
