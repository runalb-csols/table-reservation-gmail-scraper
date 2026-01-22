import base64
import re
from datetime import datetime
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# -------------------- COMMON HELPERS --------------------
def clean(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def safe_str(x) -> str:
    return clean(x) if x else ""


def decode_b64(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")


def extract_plain_and_html(payload: dict) -> tuple[str, str]:
    """
    Extracts first text/plain and first text/html found (walks nested parts).
    """
    plain = ""
    html = ""

    def walk(part):
        nonlocal plain, html
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")

        if data:
            decoded = decode_b64(data)
            if mime == "text/plain" and not plain:
                plain = decoded
            elif mime == "text/html" and not html:
                html = decoded

        for p in part.get("parts", []) or []:
            walk(p)

    walk(payload)
    return plain, html


def get_header(headers, name):
    name = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name:
            return h.get("value", "")
    return ""


def get_to_email(service) -> str:
    prof = service.users().getProfile(userId="me").execute()
    return prof.get("emailAddress", "")


def normalize_fw_prefix(subject: str) -> str:
    if not subject:
        return ""
    # remove Fw:/Fwd: multiple times
    s = subject.strip()
    while re.match(r"^(fw:|fwd:)\s*", s, flags=re.I):
        s = re.sub(r"^(fw:|fwd:)\s*", "", s, flags=re.I).strip()
    return s


def format_ddmmyyyy_from_internaldate(internal_ms: str) -> str:
    """
    Gmail internalDate is epoch ms. Used only for YEAR fallback.
    """
    try:
        dt = datetime.utcfromtimestamp(int(internal_ms) / 1000.0)
        return dt.strftime("%d-%m-%Y")
    except Exception:
        return ""


def month_name_to_number(m: str) -> int:
    m = m.strip().lower()
    months = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    return months.get(m, 0)


# -------------------- PROVIDER DETECTION --------------------
def detect_provider(subject: str, body_text: str) -> str:
    """
    IMPORTANT: Chope detection must be SUBJECT-FIRST,
    because body may not contain keyword "chope" and email can be forwarded.
    """
    subj = normalize_fw_prefix(subject).lower()
    body = (body_text or "").lower()

    # Chope: subject contains "New Reservation:"
    if "new reservation" in subj:
        return "Chope"

    # Quandoo: body contains the key fields + "Login:" is a strong signal
    if "login:" in body and ("date" in body and "time" in body and "covers" in body and "name" in body):
        return "Quandoo"

    # fallback signals
    if "quandoo" in body or "new confirmed reservation" in subj:
        return "Quandoo"

    return "Unknown"


# -------------------- PARSERS --------------------
def parse_quandoo(html: str, plain: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text("\n") if html else (plain or "")
    text_one = soup.get_text(" ") if html else (plain or "")

    # Restaurant from "Login:"
    restaurant = "Not provided"
    m = re.search(r"Login:\s*(.+)", text_one, flags=re.I)
    if m:
        restaurant = clean(m.group(1))

    # Quandoo fields
    date = "Not provided"
    m = re.search(r"DATE\s+([0-9]{2}\.[0-9]{2}\.[0-9]{4})", text, flags=re.I)
    if m:
        date = clean(m.group(1))

    time = "Not provided"
    m = re.search(r"TIME\s+([0-9]{1,2}:[0-9]{2})", text, flags=re.I)
    if m:
        time = clean(m.group(1))

    covers = "Not provided"
    m = re.search(r"COVERS\s+(\d+)", text, flags=re.I)
    if m:
        covers = clean(m.group(1))

    guest_name = "Not provided"
    m = re.search(r"NAME\s+(.+?)(PHONE|EMAIL|RES\s?#|$)", text, flags=re.I)
    if m:
        guest_name = clean(m.group(1))

    # phone/email in Quandoo are explicit rows
    phone = "Not provided"
    m = re.search(r"PHONE\s+(\+?\d{10,13})", text_one, flags=re.I)
    if m:
        phone = clean(m.group(1))

    guest_email = "Not provided"
    m = re.search(r"EMAIL\s+([\w\.-]+@[\w\.-]+\.\w+)", text_one, flags=re.I)
    if m:
        guest_email = clean(m.group(1))

    return {
        "provider": "Quandoo",
        "restaurant": restaurant,
        "date": date,
        "time": time,
        "guest_name": guest_name,
        "covers": covers,
        "phone": phone,
        "guest_email": guest_email,
    }


def parse_chope(subject: str, html: str, plain: str, internal_date_ms: str) -> dict:
    """
    Chope: DO NOT extract phone/email from body.
    Restaurant is in subject after 'at ...'
    Date/time/name mainly in subject.
    """
    subj = normalize_fw_prefix(subject)

    # Restaurant: after " at "
    restaurant = "Not provided"
    m = re.search(r"\sat\s+(.+)$", subj, flags=re.I)
    if m:
        restaurant = clean(m.group(1))

    # Time: e.g. 1:45 PM
    time = "Not provided"
    m = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", subj, flags=re.I)
    if m:
        time = clean(m.group(1).upper())

    # Guest name: "Mr. Aakash Ahuja"
    guest_name = "Not provided"
    m = re.search(r"\.\s*(Mr\.?|Ms\.?)\s+(.+?)\s+at\s", subj, flags=re.I)
    if m:
        guest_name = clean(m.group(2))
    else:
        # fallback: anything between ". " and " at "
        m2 = re.search(r"\.\s*(.+?)\s+at\s", subj, flags=re.I)
        if m2:
            guest_name = clean(m2.group(1))

    # Covers: usually in body like "4 adults"
    covers = "Not provided"
    body_text = ""
    if html:
        body_text = BeautifulSoup(html, "html.parser").get_text(" ")
    elif plain:
        body_text = plain

    m = re.search(r"(\d+)\s+adults", body_text, flags=re.I)
    if m:
        covers = clean(m.group(1))

    # Date: subject has "December 25" but no year.
    # We will format as DD-MM-YYYY using year from Gmail internalDate.
    date = "Not provided"
    m = re.search(r"New Reservation:\s*([A-Za-z]+)\s+(\d{1,2})", subj, flags=re.I)
    if m:
        month_name = m.group(1)
        day = int(m.group(2))
        month_num = month_name_to_number(month_name)

        # Get year from internal date
        y = None
        try:
            y = datetime.utcfromtimestamp(int(internal_date_ms) / 1000.0).year
        except Exception:
            y = None

        if month_num and y:
            date = f"{day:02d}-{month_num:02d}-{y}"
        else:
            # fallback to readable
            date = clean(f"{m.group(2)} {m.group(1)}")
    else:
        # fallback from internalDate only
        d = format_ddmmyyyy_from_internaldate(internal_date_ms)
        date = d if d else "Not provided"

    return {
        "provider": "Chope",
        "restaurant": restaurant,
        "date": date,
        "time": time,
        "guest_name": guest_name,
        "covers": covers,
        "phone": "Not provided",
        "guest_email": "Not provided",
    }


# -------------------- MAIN SCRAPER --------------------
def scrape_reservations(email_id: str, token_json: dict, max_results: int = 20) -> list[dict]:
    """
    Returns reservations for Chope + Quandoo.
    Fixes:
    - Correct provider detection (Chope subject first)
    - Chope never extracts phone/email from body
    - Quandoo restaurant extracted from Login:
    - Adds gmail_message_id + to_email
    - Dedup
    """
    creds = Credentials.from_authorized_user_info(token_json, SCOPES)
    service = build("gmail", "v1", credentials=creds)

    to_email = get_to_email(service)

    # Optional safety: ensure token belongs to requested mailbox
    if email_id and to_email and email_id.strip().lower() != to_email.strip().lower():
        raise ValueError(f"token_json belongs to '{to_email}', but request email_id is '{email_id}'")

    # Narrow query: target only reservation subjects/keywords
    query = '(subject:"New Reservation" OR subject:"You have a new confirmed reservation" OR quandoo OR chope) -in:chats'

    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = resp.get("messages", [])

    results = []
    seen = set()

    for m in messages:
        gmail_message_id = m.get("id")
        msg = service.users().messages().get(userId="me", id=gmail_message_id, format="full").execute()

        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        subject = get_header(headers, "Subject")

        internal_date_ms = msg.get("internalDate", "")

        plain, html = extract_plain_and_html(payload)

        # IMPORTANT: body_text for detection must be BODY ONLY (not headers)
        body_text_for_detection = ""
        if html:
            body_text_for_detection = BeautifulSoup(html, "html.parser").get_text(" ")
        else:
            body_text_for_detection = plain or ""

        provider = detect_provider(subject, body_text_for_detection)

        record = None
        if provider == "Chope":
            record = parse_chope(subject, html, plain, internal_date_ms)
        elif provider == "Quandoo":
            record = parse_quandoo(html, plain)
        else:
            continue

        # Skip records that are totally empty (reduces noise counts)
        if record.get("date") == "Not provided" and record.get("guest_name") == "Not provided":
            continue

        # Add requested fields
        record["gmail_message_id"] = gmail_message_id
        record["to_email"] = to_email
        record["requested_email_id"] = email_id

        # Dedup key (prevents duplicates)
        key = (
            record.get("provider"),
            record.get("restaurant"),
            record.get("date"),
            record.get("time"),
            record.get("guest_name"),
            record.get("covers"),
        )
        if key in seen:
            continue
        seen.add(key)

        results.append(record)

    return results
