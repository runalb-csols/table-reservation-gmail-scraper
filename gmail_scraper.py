from flask import Flask, request, jsonify
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import base64
import email
import re

app = Flask(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# ---------------------------------------------------
# Utility: Build Gmail service from token JSON
# ---------------------------------------------------
def build_gmail_service_from_token(token_json):
    """
    token_json: OAuth token data received from API request
    """
    creds = Credentials.from_authorized_user_info(token_json, SCOPES)
    service = build("gmail", "v1", credentials=creds)
    return service


# ---------------------------------------------------
# Utility: Extract email body text
# ---------------------------------------------------
def extract_email_body(payload):
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain" and "data" in part["body"]:
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
    elif "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")
    return ""


# ---------------------------------------------------
# Utility: Reservation parsing (example – extensible)
# ---------------------------------------------------
def parse_reservation(email_body):
    return {
        "provider": "Quandoo" if "quandoo" in email_body.lower() else "Unknown",
        "guest_name": "Not provided",
        "guest_email": "Not provided",
        "phone": "Not provided",
        "restaurant": "Not provided",
        "date": "Not provided",
        "time": "Not provided",
        "covers": "Not provided"
    }


# ---------------------------------------------------
# MAIN API: Scrape Gmail
# ---------------------------------------------------
@app.route("/scrape", methods=["POST"])
def scrape_gmail():
    data = request.get_json()

    # 1️⃣ Validate token presence
    if not data or "oauth_token" not in data:
        return jsonify({
            "error": "oauth_token is required in request body"
        }), 400

    token_json = data["oauth_token"]

    try:
        # 2️⃣ Build Gmail service dynamically
        service = build_gmail_service_from_token(token_json)

        # 3️⃣ Get authenticated user's email (dynamic)
        profile = service.users().getProfile(userId="me").execute()
        to_email = profile.get("emailAddress")

        # 4️⃣ Fetch emails (example query)
        query = "in:anywhere"
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=50
        ).execute()

        messages = response.get("messages", [])
        results = []

        for msg in messages:
            msg_id = msg["id"]

            message = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full"
            ).execute()

            payload = message.get("payload", {})
            email_body = extract_email_body(payload)

            reservation = parse_reservation(email_body)

            results.append({
                "gmail_message_id": msg_id,      # ✅ Gmail ID
                "to_email": to_email,             # ✅ Mailbox owner
                **reservation
            })

        return jsonify({
            "count": len(results),
            "data": results
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


# ---------------------------------------------------
# Run server
# ---------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
