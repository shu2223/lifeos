class ApiError(Exception):
    """An error the API returns as {"error": {"code", "message", "hint"}}."""

    def __init__(self, status: int, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.hint = status, code, message, hint


def not_found(what: str, ident: str, hint: str | None = None) -> ApiError:
    return ApiError(404, "not_found", f"{what} {ident!r} does not exist", hint)


def bad_request(message: str, hint: str | None = None) -> ApiError:
    return ApiError(400, "bad_request", message, hint)
