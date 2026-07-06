# routes/student_data.py — Save Student & Prefill Blueprint
#
# NEW FILE — does NOT modify college_predictor.py, main.py logic, or any
# existing file (except the two lines needed in main.py to register this
# blueprint, given separately as find-replace).
#
# Creates its own table `predictor_students` (auto-created on first import,
# does not touch cap_cutoff_data or any existing table).
#
# Routes:
#   POST   /college-predictor/students            -> save/update a student (auto-save on Predict, or manual Save button)
#   GET    /college-predictor/students             -> list all students (id, name, percentile, updated_at) for dropdown
#   GET    /college-predictor/students/search?q=..  -> search students by name
#   GET    /college-predictor/students/<id>         -> get full saved data for one student (used for prefill)

import json
from flask import Blueprint, request, jsonify
from db import get_connection, get_cursor
from logger_setup import get_logger

logger = get_logger(__name__)
student_data_bp = Blueprint("student_data", __name__)


def _ensure_table():
    """Create predictor_students table if it doesn't exist yet. Safe to call every import."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS predictor_students (
            id                    SERIAL PRIMARY KEY,
            student_name          TEXT NOT NULL,
            counsellor_name       TEXT,
            exam_type             TEXT,
            course_name           TEXT,
            admission_authority   TEXT,
            percentile            NUMERIC,
            merit_rank             TEXT,
            home_district         TEXT,
            category              TEXT,
            gender                TEXT,
            quota                 TEXT,
            pin_code              TEXT,
            cap_year              TEXT,
            cap_round             JSONB DEFAULT '[]',
            districts             JSONB DEFAULT '[]',
            branches              JSONB DEFAULT '[]',
            universities          JSONB DEFAULT '[]',
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


_ensure_table()


# ─── POST /college-predictor/students — save or update ──────
# If body contains "id", updates that record. Otherwise, if a student
# with the same name already exists, updates that record (keeps one
# record per name so re-predicting the same student doesn't create
# duplicates). Otherwise inserts a new record.
@student_data_bp.route("/college-predictor/students", methods=["POST"])
def save_student():
    data = request.get_json(silent=True) or {}

    student_id  = data.get("id")
    name        = (data.get("student_name") or "").strip()
    if not name:
        return jsonify({"error": "student_name is required"}), 400

    counsellor_name = (data.get("counsellor_name") or "").strip()

    exam_type    = data.get("exam_type", "")
    course_name  = data.get("course_name", "")
    admission_authority = data.get("admission_authority", "")
    percentile   = data.get("percentile")
    merit_rank   = str(data.get("rank", "") or "")
    home_district = data.get("home_district", "")
    category     = data.get("category", "")
    gender       = data.get("gender", "")
    quota        = data.get("quota", "")
    pin_code     = data.get("pin_code", "")
    cap_year     = data.get("cap_year", "")
    cap_round    = data.get("cap_round", [])
    districts    = data.get("districts", [])
    branches     = data.get("branches", [])
    universities = data.get("universities", [])

    if not isinstance(cap_round, list):
        cap_round = [cap_round] if cap_round else []

    conn = get_connection()
    cur = conn.cursor()

    try:
        if student_id:
            cur.execute("""
                UPDATE predictor_students SET
                    student_name = %s, counsellor_name = %s, exam_type = %s, course_name = %s,
                    admission_authority = %s, percentile = %s, merit_rank = %s,
                    home_district = %s, category = %s, gender = %s, quota = %s,
                    pin_code = %s, cap_year = %s, cap_round = %s,
                    districts = %s, branches = %s, universities = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                name, counsellor_name, exam_type, course_name, admission_authority, percentile,
                merit_rank, home_district, category, gender, quota, pin_code,
                cap_year, json.dumps(cap_round), json.dumps(districts),
                json.dumps(branches), json.dumps(universities), student_id
            ))
            if cur.rowcount == 0:
                # id didn't match anything — fall back to insert
                student_id = None

        if not student_id:
            # Update-by-name-if-exists, else insert
            cur.execute("SELECT id FROM predictor_students WHERE student_name = %s ORDER BY updated_at DESC LIMIT 1", (name,))
            existing = cur.fetchone()
            if existing:
                student_id = existing[0]
                cur.execute("""
                    UPDATE predictor_students SET
                        counsellor_name = %s, exam_type = %s, course_name = %s, admission_authority = %s,
                        percentile = %s, merit_rank = %s, home_district = %s,
                        category = %s, gender = %s, quota = %s, pin_code = %s,
                        cap_year = %s, cap_round = %s, districts = %s,
                        branches = %s, universities = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    counsellor_name, exam_type, course_name, admission_authority, percentile,
                    merit_rank, home_district, category, gender, quota, pin_code,
                    cap_year, json.dumps(cap_round), json.dumps(districts),
                    json.dumps(branches), json.dumps(universities), student_id
                ))
            else:
                cur.execute("""
                    INSERT INTO predictor_students (
                        student_name, counsellor_name, exam_type, course_name, admission_authority,
                        percentile, merit_rank, home_district, category, gender,
                        quota, pin_code, cap_year, cap_round, districts, branches, universities
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    name, counsellor_name, exam_type, course_name, admission_authority, percentile,
                    merit_rank, home_district, category, gender, quota, pin_code,
                    cap_year, json.dumps(cap_round), json.dumps(districts),
                    json.dumps(branches), json.dumps(universities)
                ))
                student_id = cur.fetchone()[0]

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.exception("[save_student] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({"message": "Student saved", "id": student_id})


# ─── GET /college-predictor/students — list for dropdown ────
@student_data_bp.route("/college-predictor/students", methods=["GET"])
def list_students():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT id, student_name, counsellor_name, percentile, category, updated_at
        FROM predictor_students
        ORDER BY updated_at DESC
        LIMIT 200
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {
            "id": r["id"],
            "student_name": r["student_name"],
            "counsellor_name": r["counsellor_name"],
            "percentile": float(r["percentile"]) if r["percentile"] is not None else None,
            "category": r["category"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        } for r in rows
    ])


# ─── GET /college-predictor/students/search?q=... ────────────
@student_data_bp.route("/college-predictor/students/search", methods=["GET"])
def search_students():
    q = request.args.get("q", "").strip()
    conn = get_connection()
    cur = get_cursor(conn)
    if q:
        cur.execute("""
            SELECT id, student_name, percentile, category, updated_at
            FROM predictor_students
            WHERE student_name ILIKE %s
            ORDER BY updated_at DESC
            LIMIT 50
        """, (f"%{q}%",))
    else:
        cur.execute("""
            SELECT id, student_name, percentile, category, updated_at
            FROM predictor_students
            ORDER BY updated_at DESC
            LIMIT 50
        """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {
            "id": r["id"],
            "student_name": r["student_name"],
            "percentile": float(r["percentile"]) if r["percentile"] is not None else None,
            "category": r["category"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        } for r in rows
    ])


# ─── GET /college-predictor/students/<id> — full data for prefill ─
@student_data_bp.route("/college-predictor/students/<int:student_id>", methods=["GET"])
def get_student(student_id):
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM predictor_students WHERE id = %s", (student_id,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    if not r:
        return jsonify({"error": "Student not found"}), 404

    return jsonify({
        "id":                   r["id"],
        "student_name":         r["student_name"],
        "counsellor_name":      r["counsellor_name"] or "",
        "exam_type":            r["exam_type"] or "",
        "course_name":          r["course_name"] or "",
        "admission_authority":  r["admission_authority"] or "",
        "percentile":           float(r["percentile"]) if r["percentile"] is not None else None,
        "rank":                 r["merit_rank"] or "",
        "home_district":        r["home_district"] or "",
        "category":             r["category"] or "",
        "gender":               r["gender"] or "",
        "quota":                r["quota"] or "",
        "pin_code":             r["pin_code"] or "",
        "cap_year":             r["cap_year"] or "",
        "cap_round":            r["cap_round"] or [],
        "districts":            r["districts"] or [],
        "branches":             r["branches"] or [],
        "universities":         r["universities"] or [],
    })