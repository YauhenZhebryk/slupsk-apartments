import os
import json
import logging
from flask import Flask, request, jsonify, render_template, send_from_directory
import db
import parser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.json.ensure_ascii = False

# Ensure DB initialized
db.init_db()

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")

@app.route("/api/apartments", methods=["GET"])
def get_apartments():
    status = request.args.get("status", "all")
    search = request.args.get("search", "")
    sort_by = request.args.get("sort_by", "created_at")
    sort_order = request.args.get("sort_order", "desc")
    apts = db.get_all_apartments(status_filter=status, search=search, sort_by=sort_by, sort_order=sort_order)
    return jsonify({"success": True, "apartments": apts, "count": len(apts)})

@app.route("/api/apartments/<int:apt_id>", methods=["GET"])
def get_single_apartment(apt_id):
    apt = db.get_apartment_by_id(apt_id)
    if not apt:
        return jsonify({"success": False, "error": "Apartment not found"}), 404
    return jsonify({"success": True, "apartment": apt})

@app.route("/api/apartments", methods=["POST"])
def create_apartment():
    data = request.json or {}
    if not data.get("url") and not data.get("title"):
        return jsonify({"success": False, "error": "URL or title required"}), 400
    apt_id = db.add_or_update_apartment(data)
    created = db.get_apartment_by_id(apt_id)
    return jsonify({"success": True, "apartment": created}), 201

@app.route("/api/apartments/<int:apt_id>", methods=["PUT"])
def update_apartment(apt_id):
    data = request.json or {}
    apt = db.get_apartment_by_id(apt_id)
    if not apt:
        return jsonify({"success": False, "error": "Apartment not found"}), 404
    
    # Merge existing and new
    merged = {**apt, **data}
    db.add_or_update_apartment(merged)
    updated = db.get_apartment_by_id(apt_id)
    return jsonify({"success": True, "apartment": updated})

@app.route("/api/apartments/<int:apt_id>", methods=["DELETE"])
def delete_apartment(apt_id):
    db.delete_apartment(apt_id)
    return jsonify({"success": True, "message": "Deleted"})

@app.route("/api/apartments/<int:apt_id>/status", methods=["POST"])
def update_status(apt_id):
    data = request.json or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"success": False, "error": "status required"}), 400
    db.update_apartment_field(apt_id, {"status": new_status})
    return jsonify({"success": True, "status": new_status})

@app.route("/api/apartments/<int:apt_id>/notes", methods=["POST"])
def update_notes(apt_id):
    data = request.json or {}
    notes = data.get("notes", "")
    db.update_apartment_field(apt_id, {"notes": notes})
    return jsonify({"success": True, "notes": notes})

@app.route("/api/apartments/<int:apt_id>/rate", methods=["POST"])
def rate_apartment(apt_id):
    data = request.json or {}
    fields = {}
    if "user_rating" in data:
        fields["user_rating"] = max(0, min(5, int(data.get("user_rating", 0))))
    if "initial_rating" in data:
        fields["initial_rating"] = max(0, min(5, int(data.get("initial_rating", 0))))
        fields["rating"] = fields["initial_rating"]
    elif "rating" in data:
        fields["initial_rating"] = max(0, min(5, int(data.get("rating", 0))))
        fields["rating"] = fields["initial_rating"]

    if not fields:
        return jsonify({"success": False, "error": "Rating value required"}), 400

    db.update_apartment_field(apt_id, fields)
    updated = db.get_apartment_by_id(apt_id)
    return jsonify({"success": True, "apartment": updated})

@app.route("/api/apartments/parse", methods=["POST"])
def parse_and_add():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "URL is required"}), 400

    try:
        parsed_data = parser.analyze_and_extract_url(url)
        # Check if custom fields passed along
        for key in ["notes", "contact_name", "contact_phone", "viewing_date", "rating", "user_rating", "initial_rating", "status"]:
            if key in data and data[key] is not None:
                parsed_data[key] = data[key]

        apt_id = db.add_or_update_apartment(parsed_data)
        saved = db.get_apartment_by_id(apt_id)
        return jsonify({"success": True, "apartment": saved})
    except Exception as e:
        logging.exception(f"Error parsing URL {url}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/criteria", methods=["GET", "POST"])
def handle_criteria():
    if request.method == "POST":
        data = request.json or {}
        db.save_criteria(data)
        return jsonify({"success": True, "criteria": db.get_criteria()})
    return jsonify({"success": True, "criteria": db.get_criteria()})

@app.route("/api/stats", methods=["GET"])
def get_stats():
    stats = db.get_stats()
    return jsonify({"success": True, "stats": stats})

@app.route("/api/export", methods=["GET"])
def export_all():
    apts = db.get_all_apartments()
    crit = db.get_criteria()
    return jsonify({
        "apartments": apts,
        "criteria": crit,
        "exported_at": str(db.datetime.now().isoformat())
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
