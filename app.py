import os
import sqlite3
from datetime import date

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "money.db")

CATEGORIES = [
    "Food", "Travel", "Rent", "Shopping", "Bills",
    "Health", "Entertainment", "Salary", "Other",
]


# ---------- Database helpers ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            amount   REAL NOT NULL,
            type     TEXT NOT NULL CHECK (type IN ('debit', 'credit')),
            category TEXT NOT NULL,
            date     TEXT NOT NULL,
            note     TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def get_limit(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'daily_limit'").fetchone()
    return float(row["value"]) if row else 0.0


# ---------- Page ----------
@app.route("/")
def home():
    return render_template("index.html", categories=CATEGORIES)


# ---------- Entries API ----------
@app.route("/api/entries", methods=["GET"])
def list_entries():
    conn = get_db()
    rows = conn.execute("SELECT * FROM entries ORDER BY date DESC, id DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/entries", methods=["POST"])
def add_entry():
    data = request.get_json(silent=True) or {}

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number."}), 400
    if amount <= 0:
        return jsonify({"error": "Amount must be greater than 0."}), 400

    entry_type = data.get("type")
    if entry_type not in ("debit", "credit"):
        return jsonify({"error": "Type must be 'debit' or 'credit'."}), 400

    category = data.get("category") or "Other"
    if category not in CATEGORIES:
        return jsonify({"error": "Unknown category."}), 400

    # The date is always set by the server, so it changes on its own every day.
    entry_date = date.today().isoformat()

    note = (data.get("note") or "").strip()

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO entries (amount, type, category, date, note) VALUES (?, ?, ?, ?, ?)",
        (amount, entry_type, category, entry_date, note),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def delete_entry(entry_id):
    conn = get_db()
    cur = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"error": "Entry not found."}), 404
    return jsonify({"deleted": entry_id})


# ---------- Limit API ----------
@app.route("/api/limit", methods=["GET"])
def read_limit():
    conn = get_db()
    limit = get_limit(conn)
    conn.close()
    return jsonify({"daily_limit": limit})


@app.route("/api/limit", methods=["PUT"])
def update_limit():
    data = request.get_json(silent=True) or {}
    try:
        limit = float(data.get("daily_limit", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Limit must be a number."}), 400
    if limit < 0:
        return jsonify({"error": "Limit cannot be negative."}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('daily_limit', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(limit),),
    )
    conn.commit()
    conn.close()
    return jsonify({"daily_limit": limit})


# ---------- Summary API (feeds the circle) ----------
@app.route("/api/summary", methods=["GET"])
def summary():
    today = date.today().isoformat()
    conn = get_db()

    limit = get_limit(conn)

    spent_today = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM entries WHERE type = 'debit' AND date = ?",
        (today,),
    ).fetchone()[0]

    total_debit = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM entries WHERE type = 'debit'"
    ).fetchone()[0]
    total_credit = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM entries WHERE type = 'credit'"
    ).fetchone()[0]

    cat_rows = conn.execute(
        "SELECT category, SUM(amount) AS total FROM entries "
        "WHERE type = 'debit' GROUP BY category ORDER BY total DESC"
    ).fetchall()
    conn.close()

    left = limit - spent_today
    percent = (spent_today / limit * 100) if limit > 0 else 0

    if limit <= 0:
        status = "no_limit"
    elif spent_today > limit:
        status = "over"
    elif percent >= 80:
        status = "near"
    else:
        status = "ok"

    return jsonify(
        {
            "date": today,
            "daily_limit": limit,
            "spent_today": round(spent_today, 2),
            "left_today": round(left, 2),
            "percent_used": round(percent, 1),
            "status": status,
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
            "balance": round(total_credit - total_debit, 2),
            "by_category": [
                {"category": r["category"], "total": round(r["total"], 2)} for r in cat_rows
            ],
        }
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)