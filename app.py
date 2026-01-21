from flask import Flask, jsonify
from gmail_scraper import scrape_reservations

app = Flask(__name__)


@app.route("/scrape", methods=["POST"])
def scrape():
    data = scrape_reservations()
    return jsonify({
        "count": len(data),
        "data": data
    })


if __name__ == "__main__":
    app.run(debug=True)
