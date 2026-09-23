"""Shared errors for the overlay operator library."""


class NotImplementedOperator(NotImplementedError):
    """Raised when an operator path is scheduled for a later plan."""

    def __init__(self, operator: str, vector: str, detail: str | None = None) -> None:
        msg = f"NotImplemented: {operator} vector='{vector}'"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.operator = operator
        self.vector = vector
