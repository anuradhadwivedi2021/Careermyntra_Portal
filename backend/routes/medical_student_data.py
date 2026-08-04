# routes/medical_student_data.py — Saved Students for the Medical Predictor
# ============================================================================
# NEW FILE — completely separate from medical_predictor.py. Every operation
# on a saved Medical Predictor student (save, list, search, get one, delete)
# lives HERE and only here, in one file, so nothing is split across
# multiple files. medical_predictor.py does not define any of these routes.
#
# Uses the `medical_predictor_students` table, which is created by
# medical_predictor.py's _ensure_medical_schema() at app startup — this
# file only reads/writes that table, it does not create it.
#
# Registering this blueprint in main.py is the only touchpoint with the
# rest of the app; nothing here can affect the Engineering predictor's
# student_data.py/student_delete.py, and nothing there can affect this.
#
# Routes in this file:
#   POST   /medical-predictor/students                -> save/update a student
#   GET    /medical-predictor/students                -> list saved students
#   GET    /medical-predictor/students/search?q=       -> search by name
#   GET    /medical-predictor/students/<id>             -> full data (prefill)
#   DELETE /medical-predictor/students/<id>             -> delete (admin password)

import os
import json
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv
from db import get_connection, get_cursor
from logger_setup import get_logger

load_dotenv()

logger = get_logger(__name__)
medical_student_data_bp = Blueprint("medical_student_data", __name__)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


# ─── Save Student — Medical Prediction Form ──────────────────────────────
# Saves: Student Details, Course, Marks, Rank, selected admission
# parameters (category/quota/gender/districts/seat types/CAP year/round/
# colleges), and Counsellor Name. One row per student name — re-saving the
# same student name updates that row instead of creating duplicates.
@medical_student_data_bp.route("/medical-predictor/students", methods=["POST"])
def save_medical_student():
    data = request.get_json(silent=True) or {}

    student_id = data.get("id")
    name = (data.get("student_name") or "").strip()
    if not name:
        return jsonify({"error": "student_name is required"}), 400

    counsellor_name = (data.get("counsellor_name") or "").strip()
    course_slug = data.get("course_slug", "")
    neet_rank = str(data.get("neet_rank", "") or "")
    neet_marks = str(data.get("neet_marks", "") or "")
    category = data.get("category", "")
    gender = data.get("gender", "")
    cap_year = data.get("cap_year", "")
    cap_round = data.get("cap_round", [])
    admission_authority = data.get("admission_authority", "")
    states = data.get("states", [])
    districts = data.get("districts", [])
    seat_types = data.get("seat_types", [])
    quotas = data.get("quotas", [])
    college_statuses = data.get("college_statuses", [])
    colleges = data.get("colleges", [])

    if not isinstance(cap_round, list):
        cap_round = [cap_round] if cap_round else []

    conn = get_connection()
    cur = conn.cursor()
    try:
        if student_id:
            cur.execute("""
                UPDATE medical_predictor_students SET
                    student_name = %s, counsellor_name = %s, course_slug = %s,
                    neet_rank = %s, neet_marks = %s, category = %s, gender = %s,
                    cap_year = %s, cap_round = %s, admission_authority = %s, states = %s,
                    districts = %s, seat_types = %s,
                    quotas = %s, college_statuses = %s, colleges = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                name, counsellor_name, course_slug, neet_rank, neet_marks, category, gender,
                cap_year, json.dumps(cap_round), admission_authority, json.dumps(states),
                json.dumps(districts), json.dumps(seat_types),
                json.dumps(quotas), json.dumps(college_statuses), json.dumps(colleges), student_id
            ))
            if cur.rowcount == 0:
                student_id = None

        if not student_id:
            cur.execute(
                "SELECT id FROM medical_predictor_students WHERE student_name = %s "
                "ORDER BY updated_at DESC LIMIT 1", (name,)
            )
            existing = cur.fetchone()
            if existing:
                student_id = existing[0]
                cur.execute("""
                    UPDATE medical_predictor_students SET
                        counsellor_name = %s, course_slug = %s, neet_rank = %s, neet_marks = %s,
                        category = %s, gender = %s, cap_year = %s, cap_round = %s,
                        admission_authority = %s, states = %s,
                        districts = %s, seat_types = %s, quotas = %s, college_statuses = %s, colleges = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    counsellor_name, course_slug, neet_rank, neet_marks, category, gender,
                    cap_year, json.dumps(cap_round), admission_authority, json.dumps(states),
                    json.dumps(districts), json.dumps(seat_types),
                    json.dumps(quotas), json.dumps(college_statuses), json.dumps(colleges), student_id
                ))
            else:
                cur.execute("""
                    INSERT INTO medical_predictor_students (
                        student_name, counsellor_name, course_slug, neet_rank, neet_marks,
                        category, gender, cap_year, cap_round, admission_authority, states,
                        districts, seat_types, quotas, college_statuses, colleges
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    name, counsellor_name, course_slug, neet_rank, neet_marks, category, gender,
                    cap_year, json.dumps(cap_round), admission_authority, json.dumps(states),
                    json.dumps(districts), json.dumps(seat_types),
                    json.dumps(quotas), json.dumps(college_statuses), json.dumps(colleges)
                ))
                student_id = cur.fetchone()[0]

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.exception("[medical_student_data:save] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({"message": "Student saved", "id": student_id})


# ─── List saved students (used by the Students modal / dropdown) ────────
@medical_student_data_bp.route("/medical-predictor/students", methods=["GET"])
def list_medical_students():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT id, student_name, counsellor_name, course_slug, neet_rank, neet_marks,
               category, cap_year, updated_at
        FROM medical_predictor_students
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
            "course_slug": r["course_slug"],
            "neet_rank": r["neet_rank"],
            "neet_marks": r["neet_marks"],
            "category": r["category"],
            "cap_year": r["cap_year"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        } for r in rows
    ])


# ─── Search saved students by name ───────────────────────────────────────
@medical_student_data_bp.route("/medical-predictor/students/search", methods=["GET"])
def search_medical_students():
    q = request.args.get("q", "").strip()
    conn = get_connection()
    cur = get_cursor(conn)
    if q:
        cur.execute("""
            SELECT id, student_name, counsellor_name, course_slug, updated_at
            FROM medical_predictor_students
            WHERE student_name ILIKE %s
            ORDER BY updated_at DESC
            LIMIT 50
        """, (f"%{q}%",))
    else:
        cur.execute("""
            SELECT id, student_name, counsellor_name, course_slug, updated_at
            FROM medical_predictor_students
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
            "counsellor_name": r["counsellor_name"],
            "course_slug": r["course_slug"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        } for r in rows
    ])


# ─── Full saved data for one student (used for form pre-fill) ───────────
@medical_student_data_bp.route("/medical-predictor/students/<int:student_id>", methods=["GET"])
def get_medical_student(student_id):
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM medical_predictor_students WHERE id = %s", (student_id,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    if not r:
        return jsonify({"error": "Student not found"}), 404

    return jsonify({
        "id": r["id"],
        "student_name": r["student_name"],
        "counsellor_name": r["counsellor_name"] or "",
        "course_slug": r["course_slug"] or "",
        "neet_rank": r["neet_rank"] or "",
        "neet_marks": r["neet_marks"] or "",
        "category": r["category"] or "",
        "gender": r["gender"] or "",
        "cap_year": r["cap_year"] or "",
        "cap_round": r["cap_round"] or [],
        "admission_authority": r["admission_authority"] or "",
        "states": r["states"] or [],
        "districts": r["districts"] or [],
        "seat_types": r["seat_types"] or [],
        "quotas": r["quotas"] or [],
        "college_statuses": r["college_statuses"] or [],
        "colleges": r["colleges"] or [],
    })


# ─── Delete a saved student (admin password protected) ──────────────────
@medical_student_data_bp.route("/medical-predictor/students/<int:student_id>", methods=["DELETE"])
def delete_medical_student(student_id):
    data = request.get_json(silent=True) or {}
    admin_password = data.get("admin_password", "")

    if not ADMIN_PASSWORD:
        logger.error("[medical_student_data:delete] ADMIN_PASSWORD not configured in .env")
        return jsonify({"error": "Admin password not configured on server"}), 500
    if not admin_password:
        return jsonify({"error": "Admin password is required"}), 400
    if admin_password != ADMIN_PASSWORD:
        return jsonify({"error": "Incorrect admin password"}), 401

    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT id, student_name FROM medical_predictor_students WHERE id = %s", (student_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Student not found"}), 404

        cur.execute("DELETE FROM medical_predictor_students WHERE id = %s", (student_id,))
        conn.commit()
        logger.info(f"[medical_student_data:delete] Deleted student id={student_id} name={row['student_name']}")
    except Exception as e:
        conn.rollback()
        logger.exception("[medical_student_data:delete] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({"message": "Student deleted", "id": student_id})