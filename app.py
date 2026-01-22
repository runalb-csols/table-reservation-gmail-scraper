from flask import Flask, request, jsonify
from gmail_scraper import scrape_reservations

app = Flask(__name__)

@app.route("/scrape", methods=["POST"])
def scrape():
    data = request.get_json(force=True)

    email_id = data.get("email_id")
    token_json = data.get("token_json")
    max_results = data.get("max_results", 20)

    if not email_id or not token_json:
        return jsonify({
            "status": "error",
            "message": "email_id and token_json are required"
        }), 400

    try:
        results = scrape_reservations(
            email_id=email_id,
            token_json=token_json,
            max_results=int(max_results)
        )
        return jsonify({
            "status": "success",
            "count": len(results),
            "data": results
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    app.run(debug=True)
