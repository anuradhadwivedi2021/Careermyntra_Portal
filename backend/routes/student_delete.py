# routes/student_delete.py — Delete Student (Admin Password Protected) Blueprint
#
# NEW FILE — does NOT modify student_data.py, main.py logic, or any
# existing file (except the two lines needed in main.py to register this
# blueprint, given separately as find-replace).
#
# Uses the existing `predictor_students` table (created by student_data.py).
#
# Routes:
#   DELETE /college-predictor/students/<id>   -> body: { "admin_password": "..." }
#                                                 Deletes the student record
#                                                 only after admin_password
#                                                 matches ADMIN_PASSWORD env var.

import os
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv
from db import get_connection, get_cursor
from logger_setup import get_logger

load_dotenv()

logger = get_logger(__name__)
student_delete_bp = Blueprint("student_delete", __name__)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


@student_delete_bp.route("/college-predictor/students/<int:student_id>", methods=["DELETE"])
def delete_student(student_id):
    data = request.get_json(silent=True) or {}
    admin_password = data.get("admin_password", "")

    if not ADMIN_PASSWORD:
        logger.error("[delete_student] ADMIN_PASSWORD not configured in .env")
        return jsonify({"error": "Admin password not configured on server"}), 500

    if not admin_password:
        return jsonify({"error": "Admin password is required"}), 400

    if admin_password != ADMIN_PASSWORD:
        return jsonify({"error": "Incorrect admin password"}), 401

    conn = get_connection()
    cur = get_cursor(conn)

    try:
        cur.execute("SELECT id, student_name FROM predictor_students WHERE id = %s", (student_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Student not found"}), 404

        cur.execute("DELETE FROM predictor_students WHERE id = %s", (student_id,))
        conn.commit()
        logger.info(f"[delete_student] Deleted student id={student_id} name={row['student_name']}")
    except Exception as e:
        conn.rollback()
        logger.exception("[delete_student] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({"message": "Student deleted", "id": student_id})