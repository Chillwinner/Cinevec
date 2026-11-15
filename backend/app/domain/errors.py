class AppError(Exception):
    """Business error mapped to an HTTP response by the presentation layer."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__("not_found", message, 404)


class ConflictError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("conflict", message, 409)


class NotConfiguredError(AppError):
    def __init__(self, message: str, code: str = "not_configured") -> None:
        super().__init__(code, message, 503)


class DependencyError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("dependency_error", message, 503)
