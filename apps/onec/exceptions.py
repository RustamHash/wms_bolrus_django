class OnecError(Exception):
    pass


class OnecConfigError(OnecError):
    pass


class OnecAuthError(OnecError):
    pass


class OnecNotFound(OnecError):
    pass


class OnecRequestError(OnecError):
    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body
