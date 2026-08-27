import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from core.session import HTTPSession
from core.url_parser import ParsedMeetingURL
from utils.logger import logger

@dataclass
class AuthResult:
    success: bool
    session_cookie: str | None = None
    user_name: str | None = None
    error_message: str | None = None

class Authenticator:
    @staticmethod
    def parse_cookie_string(cookie_header: str) -> dict[str, str]:
        """Parses a standard browser Cookie header string into a dictionary."""
        cookies = {}
        for part in cookie_header.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    @staticmethod
    def authenticate_with_cookie(session: HTTPSession, cookie_val: str, domain: str = "") -> AuthResult:
        """Injects direct BREEZESESSION or raw Cookie string into session."""
        if not cookie_val:
            return AuthResult(success=False, error_message="Cookie is empty")
            
        if "=" in cookie_val:
            cookies = Authenticator.parse_cookie_string(cookie_val)
        else:
            cookies = {"BREEZESESSION": cookie_val.strip()}
            
        session.update_cookies(cookies, domain=domain)
        breeze_val = cookies.get("BREEZESESSION")
        return AuthResult(success=True, session_cookie=breeze_val)

    @staticmethod
    def probe_guest_access(session: HTTPSession, meeting: ParsedMeetingURL) -> AuthResult:
        """Attempts to access the room as guest to acquire a valid BREEZESESSION cookie."""
        try:
            if meeting.session_token:
                session.set_cookie("BREEZESESSION", meeting.session_token, domain=meeting.host, path="/")
                
            resp = session.get(meeting.room_url, timeout=15)
            breeze_cookie = session.get_cookie("BREEZESESSION")
            if breeze_cookie:
                logger.info(f"Acquired BREEZESESSION from room: {breeze_cookie[:10]}...")
                return AuthResult(success=True, session_cookie=breeze_cookie)
                
            return AuthResult(success=False, error_message="No session cookie received in guest mode")
        except Exception as e:
            return AuthResult(success=False, error_message=f"Guest probe failed: {e}")

    @staticmethod
    def login_adobe_connect(session: HTTPSession, base_url: str, username: str, password: str) -> AuthResult:
        """Authenticates directly against Adobe Connect XML API (/api/xml?action=login)."""
        api_url = f"{base_url.rstrip('/')}/api/xml?action=login&login={urllib.parse.quote(username)}&password={urllib.parse.quote(password)}"
        try:
            resp = session.get(api_url, timeout=20)
            root = ET.fromstring(resp.text)
            status_elem = root.find(".//status")
            status_code = status_elem.attrib.get("code") if status_elem is not None else None
            
            if status_code == "ok":
                breeze = session.get_cookie("BREEZESESSION")
                logger.info("Successfully authenticated via Adobe Connect XML API")
                return AuthResult(success=True, session_cookie=breeze)
            else:
                subcode = status_elem.attrib.get("subcode", "unknown") if status_elem is not None else "unknown"
                return AuthResult(success=False, error_message=f"Login rejected by server (subcode: {subcode})")
        except Exception as e:
            return AuthResult(success=False, error_message=f"API login exception: {e}")
