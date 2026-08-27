import http.cookiejar
import io
import ssl
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Iterator
from core.config import AppConfig, DEFAULT_CONFIG
from utils.logger import logger

class Response:
    def __init__(self, raw_resp: urllib.request.addinfourl, status_code: int, url: str):
        self._raw = raw_resp
        self.status_code = status_code
        self.url = url
        # Use lower-case keys for consistent header lookup
        self.headers = {k.lower(): v for k, v in raw_resp.headers.items()}
        self._content: bytes | None = None

    @property
    def content(self) -> bytes:
        if self._content is None:
            self._content = self._raw.read()
        return self._content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="ignore")

    @property
    def raw(self):
        return self._raw

    def iter_content(self, chunk_size: int = 1024 * 64) -> Iterator[bytes]:
        while True:
            chunk = self._raw.read(chunk_size)
            if not chunk:
                break
            yield chunk

class HTTPSession:
    def __init__(self, config: AppConfig = DEFAULT_CONFIG):
        self.config = config
        self.cookie_jar = http.cookiejar.CookieJar()
        
        # Configure SSL context (ignore expired/self-signed certs)
        ctx = ssl.create_default_context()
        if not config.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        # Several AC deployments sit behind DPI/middleboxes that silently drop
        # TLS handshakes lacking an ALPN extension (curl always sends one;
        # Python does not). Advertising http/1.1 restores compatibility.
        try:
            ctx.set_alpn_protocols(["http/1.1"])
        except Exception:
            pass
            
        self.ssl_context = ctx
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=ctx)
        )
        self.headers: dict[str, str] = {
            "User-Agent": config.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
            "Connection": "keep-alive"
        }

    def set_cookie(self, name: str, value: str, domain: str = "", path: str = "/") -> None:
        cookie = http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain, domain_specified=bool(domain), domain_initial_dot=False,
            path=path, path_specified=True, secure=False, expires=None,
            discard=True, comment=None, comment_url=None, rest={'HttpOnly': None},
            rfc2109=False
        )
        self.cookie_jar.set_cookie(cookie)

    def get_cookie(self, name: str) -> str | None:
        for c in self.cookie_jar:
            if c.name == name:
                return c.value
        return None

    def update_cookies(self, cookie_dict: dict[str, str], domain: str = "") -> None:
        for k, v in cookie_dict.items():
            self.set_cookie(k, v, domain=domain)

    def get(self, url: str, headers: dict[str, str] | None = None, stream: bool = False, timeout: int | None = None, retries: int = 2) -> Response:
        req_headers = dict(self.headers)
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, headers=req_headers, method="GET")
        timeout_val = timeout or self.config.request_timeout

        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.opener.open(req, timeout=timeout_val)
                return Response(resp, resp.status, resp.geturl())
            except urllib.error.HTTPError as e:
                return Response(e, e.code, url)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                # Transient TLS/connect failures are common on saturated links;
                # a fresh connection attempt rebuilds the handshake from scratch.
                last_exc = e
                if attempt < retries:
                    logger.debug(f"Retrying GET ({attempt + 1}/{retries}) after transport error: {e}")
                    time.sleep(1.5 * (attempt + 1))
        raise last_exc

    def post(self, url: str, data: dict[str, str] | None = None, headers: dict[str, str] | None = None, timeout: int | None = None, retries: int = 2) -> Response:
        req_headers = dict(self.headers)
        if headers:
            req_headers.update(headers)

        encoded_data = None
        if data is not None:
            encoded_data = urllib.parse.urlencode(data).encode("utf-8")
            req_headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=encoded_data, headers=req_headers, method="POST")
        timeout_val = timeout or self.config.request_timeout

        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.opener.open(req, timeout=timeout_val)
                return Response(resp, resp.status, resp.geturl())
            except urllib.error.HTTPError as e:
                return Response(e, e.code, url)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last_exc = e
                if attempt < retries:
                    logger.debug(f"Retrying POST ({attempt + 1}/{retries}) after transport error: {e}")
                    time.sleep(1.5 * (attempt + 1))
        raise last_exc

class SessionManager:
    @staticmethod
    def create_session(config: AppConfig = DEFAULT_CONFIG, cookies: dict[str, str] | None = None) -> HTTPSession:
        session = HTTPSession(config)
        if cookies:
            session.update_cookies(cookies)
        return session
