# routes/course_admin.py — Add New College Predictor Course (Admin Password Protected)
# ============================================================================
# NEW FILE — does NOT modify college_predictor.py or any other existing file.
# Only 2 lines need to be added to main.py to register this blueprint
# (given separately below as find/replace).
#
# What this solves:
#   Currently, adding a new course to the College Predictor (B.E./B.Tech,
#   Pharmacy, MBA, etc.) required a developer to manually:
#     1. Write a new CREATE TABLE migration for predictor_data_<slug>
#     2. Manually INSERT a row into predictor_courses
#   This file adds an Admin-only API that does both automatically, so a
#   new course card can be added at runtime from the Admin Dashboard —
#   no code deploy needed.
#
# Route:
#   POST /college-predictor/courses/new
#   Body (JSON):
#     {
#       "admin_password": "...",
#       "slug": "pharmacy",              -> used in URLs, lowercase, no spaces
#       "display_name": "B.Pharm",       -> shown on the course card
#       "icon": "💊",                    -> optional, defaults to a generic icon
#       "display_order": 2               -> optional, defaults to next available
#     }
#
#   On success, creates:
#     - A new table `predictor_data_<slug>` with the SAME structure/columns
#       as predictor_data_be_btech (so all existing upload/predict/filter
#       code in college_predictor.py works for the new course immediately,
#       since it already resolves table_name dynamically via course_slug).
#     - A new row in predictor_courses so the course card shows up
#       automatically in GET /college-predictor/available-courses.
#
#   This does NOT touch any other course's table or data.
#
# Independent per-course configuration this enables out of the box:
#   - Its own dataset / its own dedicated table         -> YES (new table per course)
#   - Its own Excel upload, mapped to its own table     -> YES (upload-cutoff/<slug>)
#   - Its own card, independently addable at runtime    -> YES (this endpoint)
#   Still shared across all courses (by design, per the "same initially,
#   customizable later" requirement) unless separately built later:
#   - Required Excel column list (hardcoded in college_predictor.py)
#   - Form fields shown to the student
#   - Prediction/probability formula, PDF layout, result columns

import os
import re
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv
from db import get_connection, get_cursor
from logger_setup import get_logger

load_dotenv()

logger = get_logger(__name__)
course_admin_bp = Blueprint("course_admin", __name__)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")


@course_admin_bp.route("/college-predictor/courses/new", methods=["POST"])
def add_new_predictor_course():
    data = request.get_json(silent=True) or {}

    admin_password = data.get("admin_password", "")
    slug = (data.get("slug") or "").strip().lower()
    display_name = (data.get("display_name") or "").strip()
    icon = (data.get("icon") or "📁").strip()
    display_order = data.get("display_order")

    # ── Admin password check (same pattern as student_delete.py) ──
    if not ADMIN_PASSWORD:
        logger.error("[add_new_predictor_course] ADMIN_PASSWORD not configured in .env")
        return jsonify({"error": "Admin password not configured on server"}), 500
    if not admin_password:
        return jsonify({"error": "Admin password is required"}), 400
    if admin_password != ADMIN_PASSWORD:
        return jsonify({"error": "Incorrect admin password"}), 401

    # ── Validate inputs ──
    if not display_name:
        return jsonify({"error": "display_name is required"}), 400
    if not slug:
        # auto-derive a slug from display_name if not given
        slug = re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_")
    if not SLUG_RE.match(slug):
        return jsonify({
            "error": "slug must start with a letter and contain only lowercase "
                     "letters, numbers, or underscores (2-50 chars)"
        }), 400

    table_name = f"predictor_data_{slug}"

    conn = get_connection()
    cur = get_cursor(conn)

    try:
        # ── Reject if slug or table already exists ──
        cur.execute("SELECT id FROM predictor_courses WHERE slug = %s", (slug,))
        if cur.fetchone():
            return jsonify({"error": f"A course with slug '{slug}' already exists"}), 409

        cur.execute("SELECT to_regclass(%s) AS exists_check", (table_name,))
        row = cur.fetchone()
        if row and row["exists_check"]:
            return jsonify({"error": f"Table '{table_name}' already exists"}), 409

        # ── Determine display_order if not supplied ──
        if display_order is None:
            cur.execute("SELECT COALESCE(MAX(display_order), 0) + 1 AS next_order FROM predictor_courses")
            display_order = cur.fetchone()["next_order"]

        # ── Create the dedicated table for this course ──
        # Same structure as predictor_data_be_btech (see migrations/add_predictor_courses.sql)
        # so every existing upload/predict/filter route in college_predictor.py
        # works immediately — those routes only ever reference table_name
        # dynamically, never a hardcoded table.
        create_sql = f"""
            CREATE TABLE {table_name} (
                id SERIAL PRIMARY KEY,
                college_code VARCHAR(30) NOT NULL,
                college_name VARCHAR(300) NOT NULL,
                branch_name VARCHAR(200) NOT NULL,
                branch_code TEXT,
                district VARCHAR(100),
                university VARCHAR(300),
                cap_year VARCHAR(20) NOT NULL,
                cap_round VARCHAR(50) NOT NULL,
                category VARCHAR(50) NOT NULL,
                sub_category VARCHAR(50),
                seat_type VARCHAR(50) DEFAULT 'AI',
                exam_type VARCHAR(50) DEFAULT 'MHT-CET',
                gender VARCHAR(10),
                quota_code VARCHAR(10) DEFAULT 'S',
                course_name VARCHAR(200),
                cutoff_percentile NUMERIC(7,4),
                cutoff_score NUMERIC(8,2),
                fees NUMERIC(10,2),
                naac_grade VARCHAR(10),
                nba_accredited VARCHAR(10) DEFAULT 'No',
                placement_highest NUMERIC(12,2),
                placement_average NUMERIC(8,2),
                website VARCHAR(300),
                address TEXT,
                location TEXT,
                admission_authority VARCHAR(200),
                is_autonomous BOOLEAN DEFAULT false,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT {slug}_unique UNIQUE (
                    college_name, branch_name, cap_year, cap_round, category,
                    sub_category, seat_type, gender, quota_code, course_name, exam_type
                )
            );
        """
        cur.execute(create_sql)

        cur.execute(f"CREATE INDEX idx_{slug}_branch   ON {table_name} (branch_name);")
        cur.execute(f"CREATE INDEX idx_{slug}_district ON {table_name} (district);")
        cur.execute(f"CREATE INDEX idx_{slug}_filter    ON {table_name} (exam_type, category, cap_year);")

        # ── Register the course so it shows up as a card immediately ──
        cur.execute(
            """
            INSERT INTO predictor_courses (slug, display_name, icon, table_name, display_order, is_active)
            VALUES (%s, %s, %s, %s, %s, true)
            RETURNING id
            """,
            (slug, display_name, icon, table_name, display_order)
        )
        new_id = cur.fetchone()["id"]

        conn.commit()
        logger.info(f"[add_new_predictor_course] Created course '{slug}' (id={new_id}, table={table_name})")

        return jsonify({
            "message": f"Course '{display_name}' created successfully",
            "id": new_id,
            "slug": slug,
            "table_name": table_name,
            "display_order": display_order,
        }), 201

    except Exception as e:
        conn.rollback()
        logger.exception("[add_new_predictor_course] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@course_admin_bp.route("/college-predictor/courses/<int:course_id>", methods=["DELETE"])
def delete_predictor_course(course_id):
    """
    Admin-only: deactivate (soft-delete) a course so its card disappears
    from the course picker. Does NOT drop the underlying data table —
    the data is preserved in case the course needs to be restored later.
    Body: { "admin_password": "..." }
    """
    data = request.get_json(silent=True) or {}
    admin_password = data.get("admin_password", "")

    if not ADMIN_PASSWORD:
        return jsonify({"error": "Admin password not configured on server"}), 500
    if not admin_password:
        return jsonify({"error": "Admin password is required"}), 400
    if admin_password != ADMIN_PASSWORD:
        return jsonify({"error": "Incorrect admin password"}), 401

    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT id, slug FROM predictor_courses WHERE id = %s", (course_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Course not found"}), 404

        cur.execute("UPDATE predictor_courses SET is_active = false WHERE id = %s", (course_id,))
        conn.commit()
        logger.info(f"[delete_predictor_course] Deactivated course id={course_id} slug={row['slug']}")
        return jsonify({"message": f"Course '{row['slug']}' deactivated (data preserved)"})
    except Exception as e:
        conn.rollback()
        logger.exception("[delete_predictor_course] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()