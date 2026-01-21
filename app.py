from flask import Flask, request, jsonify
from gmail_scraper import scrape_reservations

app = Flask(__name__)

@app.route("/scrape", methods=["POST"])
def scrape():
    try:
        data = request.get_json(force=True)

        email_id = data.get("email_id")
        token_json = data.get("token_json")

        if not email_id or not token_json:
            return jsonify({
                "status": "error",
                "message": "email_id and token_json are required"
            }), 400

        results = scrape_reservations(
            email_id=email_id,
            token_json=token_json
        )

        return jsonify({
            "status": "success",
            "count": len(results),
            "data": results
        }), 200

    except Exception as e:
        # IMPORTANT: always return JSON, not HTML
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    app.run(debug=True)
