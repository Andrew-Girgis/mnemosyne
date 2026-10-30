"""Domain errors carry an HTTP status and a stable code for the API/CLI contract."""


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def not_found(what: str, ref: str) -> DomainError:
    return DomainError(404, "not_found", f"no {what} {ref!r}")


def forbidden(message: str) -> DomainError:
    return DomainError(403, "forbidden", message)
