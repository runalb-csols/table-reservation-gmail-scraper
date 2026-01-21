import base64
import re
from typing import Dict, Any, List

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
MAX_RESULTS = 50
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _decode(data: str) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_text(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""

    if payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])

    collected = []

    def walk(parts):
        for p in parts:
            mime = p.get("mimeType", "")
            body = p.get("body", {})
            if mime in ("text/plain", "text/html") and body.get("data"):
                collected.append(_decode(body["data"]))
            if p.get("parts"):
                walk(p["parts"])

    if payload.get("parts"):
        walk(payload["parts"])

    return "\n".join([c for c in collected if c]).strip()


def gmail_get(url: str, token: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    # IMPORTANT: show clean error if token invalid
    if r.status_code == 401:
        raise Exception("401 Unauthorized: Access token is invalid/expired OR missing Gmail scope.")
    r.raise_for_status()
    return r.json()


def build_access_token(token_json: Dict[str, Any]) -> str:
    """
    Accepts token_json from API body. Works in two modes:
    A) token only (must be fresh)
    B) token + refresh_token + token_uri + client_id + client_secret -> auto-refresh
    """
    token = token_json.get("token")
    refresh_token = token_json.get("refresh_token")
    token_uri = token_json.get("token_uri")
    client_id = token_json.get("client_id")
    client_secret = token_json.get("client_secret")

    # If refresh fields are present, auto-refresh to a valid access token
    if refresh_token and token_uri and client_id and client_secret:
        creds = Credentials(
            token=token,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        # refresh if needed
        req = GoogleRequest()
        creds.refresh(req)
        return creds.token

    # Otherwise fall back to token-only
    if not token:
        raise ValueError("token_json must include at least 'token'")
    return token


def detect_provider(subject: str, body_text: str, from_header: str) -> str:
    s = (subject or "").lower()
    b = (body_text or "").lower()
    f = (from_header or "").lower()

    if "quandoo" in s or "quandoo" in b or "quandoo" in f or "confirmed reservation" in s:
        return "Quandoo"
    if "new reservation" in s or "chope" in s or "chope" in b or "chope" in f:
        return "Chope"
    return "Unknown"


def parse_quandoo(text: str) -> Dict[str, str]:
    def find(pattern: str) -> str:
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else "Not provided"

    return {
        "date": find(r"DATE\s+([0-9]{2}\.[0-9]{2}\.[0-9]{4})"),
        "time": find(r"TIME\s+([0-9]{1,2}:[0-9]{2})"),
        "covers": find(r"COVERS\s+(\d+)"),
        "guest_name": find(r"NAME\s+(.+)"),
        "phone": find(r"PHONE\s+(\+?\d{10,15})"),
        "guest_email": find(r"EMAIL\s+([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})"),
    }


def parse_chope(subject: str, body_text: str) -> Dict[str, str]:
    date = "Not provided"
    time = "Not provided"
    guest_name = "Not provided"
    covers = "Not provided"

    m_dt = re.search(
        r"New Reservation:\s*([A-Za-z]+)\s+(\d{1,2}),\s*([0-9]{1,2}:[0-9]{2}\s*(AM|PM))",
        subject,
        re.IGNORECASE,
    )
    if m_dt:
        month = m_dt.group(1).strip()
        day = m_dt.group(2).strip()
        time = m_dt.group(3).strip()
        date = f"{day} {month} 2025"

    m_name = re.search(r"\b(Mr\.|Ms\.|Mrs\.)\s+([A-Za-z ]+?)\s+at\b", subject)
    if m_name:
        guest_name = m_name.group(2).strip()

    m_cov = re.search(r"(\d+)\s*(adults|adult|covers|guests|people|pax)", body_text, re.IGNORECASE)
    if m_cov:
        covers = m_cov.group(1).strip()

    return {
        "date": date,
        "time": time,
        "covers": covers,
        "guest_name": guest_name,
        "phone": "Not provided",
        "guest_email": "Not provided",
    }


def scrape_reservations(email_id: str, token_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    access_token = build_access_token(token_json)

    query = '(subject:"New Reservation" OR subject:"confirmed reservation" OR quandoo OR chope) -in:chats'
    search_url = f"{GMAIL_API}/users/me/messages"
    search_data = gmail_get(search_url, access_token, params={"q": query, "maxResults": MAX_RESULTS})
    messages = search_data.get("messages", [])

    results: List[Dict[str, Any]] = []

    for m in messages:
        msg_id = m.get("id")
        if not msg_id:
            continue

        msg_url = f"{GMAIL_API}/users/me/messages/{msg_id}"
        msg = gmail_get(msg_url, access_token, params={"format": "full"})

        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        def header(name: str) -> str:
            for h in headers:
                if h.get("name", "").lower() == name.lower():
                    return h.get("value", "")
            return ""

        subject = header("Subject") or ""
        from_header = header("From") or ""

        if subject.lower().startswith(("re:", "fw: re:", "fwd: re:")):
            continue

        body_text = extract_text(payload)
        provider = detect_provider(subject, body_text, from_header)
        if provider == "Unknown":
            continue

        if provider == "Quandoo":
            parsed = parse_quandoo(body_text)
            restaurant = "Not provided"
        else:
            parsed = parse_chope(subject, body_text)
            restaurant = "Akasa - Authentic North Indian Restaurant"

        results.append({
            "gmail_message_id": msg_id,
            "to_email": email_id,
            "provider": provider,
            "restaurant": restaurant,
            "date": parsed.get("date", "Not provided"),
            "time": parsed.get("time", "Not provided"),
            "guest_name": parsed.get("guest_name", "Not provided"),
            "covers": parsed.get("covers", "Not provided"),
            "phone": parsed.get("phone", "Not provided"),
            "guest_email": parsed.get("guest_email", "Not provided"),
        })

    return results
