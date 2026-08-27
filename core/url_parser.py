import re
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, urlunparse

@dataclass
class ParsedMeetingURL:
    original_url: str
    scheme: str
    host: str
    port: int | None
    path: str
    meeting_id: str
    base_url: str        # e.g. https://tuvc2.tabrizu.ac.ir
    room_url: str        # e.g. https://tuvc2.tabrizu.ac.ir/ppnxoh87231e
    session_token: str | None
    output_zip_url: str  # e.g. https://tuvc2.tabrizu.ac.ir/ppnxoh87231e/output/ppnxoh87231e.zip?download=zip
    mainstream_url: str  # e.g. https://tuvc2.tabrizu.ac.ir/ppnxoh87231e/output/mainstream.xml

class URLParser:
    @staticmethod
    def parse(url: str) -> ParsedMeetingURL:
        """Parses and normalizes any Adobe Connect meeting or recording URL."""
        cleaned_url = url.strip()
        if not cleaned_url.startswith("http://") and not cleaned_url.startswith("https://"):
            cleaned_url = "https://" + cleaned_url
            
        parsed = urlparse(cleaned_url)
        scheme = parsed.scheme or "https"
        host = parsed.hostname or ""
        port = parsed.port
        path = parsed.path.strip("/")
        
        # Parse query params for session tokens
        query_params = parse_qs(parsed.query)
        session_token = None
        for key in ["session", "ticket", "breeze", "BREEZESESSION", "token"]:
            if key in query_params:
                session_token = query_params[key][0]
                break
                
        # Strip system endpoints to find the base room path
        room_path_parts = []
        for p in path.split("/"):
            if not p: continue
            if p in ["output", "launcher", "system"] or p.endswith(".zip") or p.endswith(".flv"):
                break
            room_path_parts.append(p)
            
        if not room_path_parts:
            raise ValueError(f"Could not extract meeting path from URL: {url}")
            
        meeting_id = room_path_parts[-1] # use the last segment as a readable identifier
        room_path = "/".join(room_path_parts)
        
        # Build base URL and normalized room URL
        netloc = f"{host}:{port}" if port and port not in (80, 443) else host
        base_url = f"{scheme}://{netloc}"
        room_url = f"{base_url}/{room_path}"
        
        output_zip_url = f"{room_url}/output/{meeting_id}.zip?download=zip"
        mainstream_url = f"{room_url}/output/mainstream.xml"
        
        return ParsedMeetingURL(
            original_url=url,
            scheme=scheme,
            host=host,
            port=port,
            path=path,
            meeting_id=meeting_id,
            base_url=base_url,
            room_url=room_url,
            session_token=session_token,
            output_zip_url=output_zip_url,
            mainstream_url=mainstream_url
        )
