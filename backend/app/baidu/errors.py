class BaiduError(Exception):
    def __init__(self, errno: int | str, message: str = "", request_id: str | None = None):
        self.errno = errno
        self.message = message
        self.request_id = request_id
        super().__init__(f"errno={errno} {message}".strip())


class AuthError(BaiduError):
    pass


class RateLimited(BaiduError):
    pass


# 官方错误码中与鉴权/频控相关的部分
TOKEN_INVALID_ERRNOS = {-6, 111, 110}
RATE_LIMIT_ERRNOS = {31034}

# listall 未开放给本应用时返回此错误码，此时改用 list 逐层遍历
SCOPE_DENIED_ERRNO = 20020
