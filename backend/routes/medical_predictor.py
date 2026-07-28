# routes/medical_predictor.py — Medical Admission Prediction Module
# ============================================================================
# NEW FILE — completely independent from routes/college_predictor.py.
# Does NOT import from, call, or share any table with the Engineering
# predictor. Registering this blueprint in main.py is the only touchpoint
# with the rest of the app; nothing here can affect Engineering/other
# modules, and nothing in Engineering can affect this module.
#
# Why a separate module (not just another row in predictor_courses):
#   - Medical admissions run on NEET Marks / NEET Rank, not Percentile.
#     The whole prediction/probability logic is rank-based (lower rank is
#     better) instead of percentile-based (higher is better) — a
#     fundamentally different comparison direction, so sharing code with
#     the Engineering predict() would require branching logic everywhere.
#   - Medical seat types (Government/Private/Deemed/Trust) and quotas
#     (State/AIQ/Management/NRI/Institutional) differ from Engineering's
#     Home/Outside/State university-quota model.
#   - Keeping them separate means a bug or future change in one module
#     can never break the other.
#
# Scalability — adding a new medical course later (BAMS, BHMS, Nursing,
# BPT, BOT, BASLP, B.Sc. Nursing, ...) requires ZERO code changes:
#   Admin uses POST /medical-predictor/courses/new (see courses/new below)
#   -> creates a new medical_data_<slug> table (same structure as MBBS/BDS)
#   -> registers it in medical_courses
#   -> the new course card appears automatically via /available-courses
#
# Routes in this file:
#   GET    /medical-predictor/available-courses
#   POST   /medical-predictor/courses/new                (admin password)
#   DELETE /medical-predictor/courses/<id>                (admin password)
#   POST   /medical-predictor/upload-cutoff/<course_slug>
#   GET    /medical-predictor/filter-options?course_slug=
#   GET    /medical-predictor/districts?course_slug=
#   GET    /medical-predictor/colleges?course_slug=&districts=
#   POST   /medical-predictor/predict
#   GET    /medical-predictor/stats?course_slug=
#   POST   /medical-predictor/download-pdf
#   DELETE /medical-predictor/clear?course_slug=          (admin password)

import os
import re
import io
import pandas as pd
from flask import Blueprint, request, jsonify, send_file
from dotenv import load_dotenv
from db import get_connection, get_cursor
from logger_setup import get_logger

load_dotenv()

logger = get_logger(__name__)
medical_predictor_bp = Blueprint("medical_predictor", __name__)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")


# ─── Table resolution — single source of truth ───────────────────────────
def _get_table_name(course_slug):
    if not course_slug:
        course_slug = "mbbs"
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(
        "SELECT table_name FROM medical_courses WHERE slug = %s AND is_active = true",
        (course_slug,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["table_name"] if row else None


def _check_admin(data):
    """Returns an error response tuple if the admin password is wrong/missing, else None."""
    admin_password = data.get("admin_password", "")
    if not ADMIN_PASSWORD:
        return jsonify({"error": "Admin password not configured on server"}), 500
    if not admin_password:
        return jsonify({"error": "Admin password is required"}), 400
    if admin_password != ADMIN_PASSWORD:
        return jsonify({"error": "Incorrect admin password"}), 401
    return None


# ─── Rank/Marks-based probability (opposite direction from percentile) ───
# A LOWER NEET rank is BETTER. So a student is more likely admitted the
# further their rank is BELOW (numerically less than) the cutoff rank.
def _calc_probability(student_rank, cutoff_rank):
    if student_rank is None or cutoff_rank is None:
        return {"pct": 15, "label": "Unknown"}
    try:
        student_rank = int(student_rank)
        cutoff_rank = int(cutoff_rank)
    except (TypeError, ValueError):
        return {"pct": 15, "label": "Unknown"}

    # Positive diff = student's rank is BETTER (lower number) than the
    # last-admitted rank at this college/category last year.
    diff_ratio = (cutoff_rank - student_rank) / max(cutoff_rank, 1)

    if diff_ratio >= 0.20:  return {"pct": 99, "label": "Very High"}
    if diff_ratio >= 0.10:  return {"pct": 92, "label": "High"}
    if diff_ratio >= 0.02:  return {"pct": 80, "label": "High"}
    if diff_ratio >= -0.02: return {"pct": 60, "label": "Medium"}
    if diff_ratio >= -0.08: return {"pct": 40, "label": "Low"}
    if diff_ratio >= -0.15: return {"pct": 20, "label": "Very Low"}
    return {"pct": 8, "label": "Very Low"}


def _chance_label(diff_ratio):
    if diff_ratio >= 0.10:
        return "Safe"
    elif diff_ratio >= -0.02:
        return "Moderate"
    else:
        return "Dream"


# ─── Available courses (drives the course-card picker) ──────────────────
@medical_predictor_bp.route("/medical-predictor/available-courses", methods=["GET"])
def get_available_courses():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT id, slug, display_name, icon, table_name, exam_type, display_order
            FROM medical_courses
            WHERE is_active = true
            ORDER BY display_order, id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "courses": [dict(r) for r in rows]})
    except Exception as e:
        logger.exception("[medical:get_available_courses] failed")
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Admin: add a new medical course at runtime (no code changes) ───────
@medical_predictor_bp.route("/medical-predictor/courses/new", methods=["POST"])
def add_new_medical_course():
    data = request.get_json(silent=True) or {}
    err = _check_admin(data)
    if err:
        return err

    display_name = (data.get("display_name") or "").strip()
    slug = (data.get("slug") or "").strip().lower()
    icon = (data.get("icon") or "⚕️").strip()
    display_order = data.get("display_order")

    if not display_name:
        return jsonify({"error": "display_name is required"}), 400
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_")
    if not SLUG_RE.match(slug):
        return jsonify({
            "error": "slug must start with a letter and contain only lowercase "
                     "letters, numbers, or underscores (2-50 chars)"
        }), 400

    table_name = f"medical_data_{slug}"

    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT id FROM medical_courses WHERE slug = %s", (slug,))
        if cur.fetchone():
            return jsonify({"error": f"A medical course with slug '{slug}' already exists"}), 409

        cur.execute("SELECT to_regclass(%s) AS exists_check", (table_name,))
        row = cur.fetchone()
        if row and row["exists_check"]:
            return jsonify({"error": f"Table '{table_name}' already exists"}), 409

        if display_order is None:
            cur.execute("SELECT COALESCE(MAX(display_order), 0) + 1 AS next_order FROM medical_courses")
            display_order = cur.fetchone()["next_order"]

        # Same NEET-based structure as MBBS/BDS — see add_medical_courses.sql
        cur.execute(f"""
            CREATE TABLE {table_name} (
                id SERIAL PRIMARY KEY,
                college_code VARCHAR(30) NOT NULL,
                college_name VARCHAR(300) NOT NULL,
                course_name VARCHAR(100) NOT NULL DEFAULT '{display_name.replace("'", "''")}',
                category VARCHAR(50) NOT NULL,
                sub_category VARCHAR(50),
                seat_type VARCHAR(50) DEFAULT 'Government',
                quota_code VARCHAR(50) DEFAULT 'State',
                gender VARCHAR(20),
                cap_year VARCHAR(20) NOT NULL,
                cap_round VARCHAR(50) NOT NULL,
                neet_marks_cutoff NUMERIC(7,2),
                neet_rank_cutoff INTEGER,
                fees NUMERIC(12,2),
                university VARCHAR(300),
                district VARCHAR(100),
                location TEXT,
                address TEXT,
                naac_grade VARCHAR(10),
                nba_accredited VARCHAR(10) DEFAULT 'No',
                website VARCHAR(300),
                admission_authority VARCHAR(200),
                is_autonomous BOOLEAN DEFAULT false,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT {slug}_unique UNIQUE (
                    college_name, course_name, cap_year, cap_round, category,
                    sub_category, seat_type, quota_code, gender
                )
            );
        """)
        cur.execute(f"CREATE INDEX idx_{slug}_district ON {table_name} (district);")
        cur.execute(f"CREATE INDEX idx_{slug}_filter   ON {table_name} (category, cap_year);")

        cur.execute(
            """
            INSERT INTO medical_courses (slug, display_name, icon, table_name, exam_type, display_order, is_active)
            VALUES (%s, %s, %s, %s, 'NEET', %s, true)
            RETURNING id
            """,
            (slug, display_name, icon, table_name, display_order)
        )
        new_id = cur.fetchone()["id"]
        conn.commit()
        logger.info(f"[medical:add_course] Created '{slug}' (id={new_id}, table={table_name})")

        return jsonify({
            "message": f"Medical course '{display_name}' created successfully",
            "id": new_id, "slug": slug, "table_name": table_name,
        }), 201

    except Exception as e:
        conn.rollback()
        logger.exception("[medical:add_course] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@medical_predictor_bp.route("/medical-predictor/courses/<int:course_id>", methods=["DELETE"])
def deactivate_medical_course(course_id):
    data = request.get_json(silent=True) or {}
    err = _check_admin(data)
    if err:
        return err

    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT id, slug FROM medical_courses WHERE id = %s", (course_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Course not found"}), 404
        cur.execute("UPDATE medical_courses SET is_active = false WHERE id = %s", (course_id,))
        conn.commit()
        return jsonify({"message": f"Medical course '{row['slug']}' deactivated (data preserved)"})
    except Exception as e:
        conn.rollback()
        logger.exception("[medical:deactivate_course] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ─── Upload cutoff Excel/CSV for a given medical course ──────────────────
REQUIRED_COLUMNS = [
    "college_name", "category", "cap_year", "cap_round",
]

@medical_predictor_bp.route("/medical-predictor/upload-cutoff/<course_slug>", methods=["POST"])
def upload_cutoff(course_slug):
    table_name = _get_table_name(course_slug)
    if not table_name:
        return jsonify({"error": f"Unknown or inactive medical course '{course_slug}'"}), 400

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]

    try:
        if file.filename.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(file)
        else:
            df = pd.read_csv(file)
    except Exception as e:
        return jsonify({"error": f"Could not read file: {e}"}), 400

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return jsonify({"error": f"Missing required columns: {', '.join(missing)}"}), 400

    conn = get_connection()
    cur = get_cursor(conn)
    inserted, skipped = 0, 0

    for _, r in df.iterrows():
        try:
            college_name = str(r.get("college_name", "")).strip()
            if not college_name or college_name.lower() == "nan":
                skipped += 1
                continue

            def val(col, default=None):
                v = r.get(col, default)
                if pd.isna(v):
                    return default
                return v

            cur.execute(f"""
                INSERT INTO {table_name} (
                    college_code, college_name, course_name, category, sub_category,
                    seat_type, quota_code, gender, cap_year, cap_round,
                    neet_marks_cutoff, neet_rank_cutoff, fees, university, district,
                    location, address, naac_grade, nba_accredited, website,
                    admission_authority, is_autonomous
                ) VALUES (
                    %s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s
                )
                ON CONFLICT (college_name, course_name, cap_year, cap_round, category,
                             sub_category, seat_type, quota_code, gender)
                DO UPDATE SET
                    neet_marks_cutoff = EXCLUDED.neet_marks_cutoff,
                    neet_rank_cutoff  = EXCLUDED.neet_rank_cutoff,
                    fees = EXCLUDED.fees,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                str(val("college_code", "")).strip(),
                college_name,
                str(val("course_name", course_slug.upper())).strip(),
                str(val("category", "")).strip(),
                str(val("sub_category", "")).strip() or None,
                str(val("seat_type", "Government")).strip(),
                str(val("quota_code", "State")).strip(),
                str(val("gender", "")).strip() or None,
                str(val("cap_year", "")).strip(),
                str(val("cap_round", "")).strip(),
                val("neet_marks_cutoff"),
                val("neet_rank_cutoff"),
                val("fees"),
                str(val("university", "")).strip() or None,
                str(val("district", "")).strip() or None,
                str(val("location", "")).strip() or None,
                str(val("address", "")).strip() or None,
                str(val("naac_grade", "")).strip() or None,
                str(val("nba_accredited", "No")).strip(),
                str(val("website", "")).strip() or None,
                str(val("admission_authority", "")).strip() or None,
                bool(val("is_autonomous", False)),
            ))
            inserted += 1
        except Exception:
            conn.rollback()
            skipped += 1
            logger.exception(f"[medical:upload] row failed for '{college_name}'")

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "message": f"Upload complete. {inserted} rows saved, {skipped} skipped.",
        "inserted": inserted, "skipped": skipped,
    })


# ─── Filter options (categories, years, rounds, seat types, quotas) ─────
@medical_predictor_bp.route("/medical-predictor/filter-options", methods=["GET"])
def get_filter_options():
    course_slug = request.args.get("course_slug", "mbbs")
    table_name = _get_table_name(course_slug)
    if not table_name:
        return jsonify({"years": [], "categories": [], "rounds": [], "seat_types": [], "quotas": [], "genders": []})

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"SELECT DISTINCT cap_year FROM {table_name} WHERE cap_year IS NOT NULL ORDER BY cap_year DESC")
    years = [r["cap_year"] for r in cur.fetchall()]
    cur.execute(f"SELECT DISTINCT category FROM {table_name} WHERE category IS NOT NULL ORDER BY category")
    categories = [r["category"] for r in cur.fetchall()]
    cur.execute(f"SELECT DISTINCT cap_round FROM {table_name} WHERE cap_round IS NOT NULL ORDER BY cap_round")
    rounds = [r["cap_round"] for r in cur.fetchall()]
    cur.execute(f"SELECT DISTINCT seat_type FROM {table_name} WHERE seat_type IS NOT NULL ORDER BY seat_type")
    seat_types = [r["seat_type"] for r in cur.fetchall()]
    cur.execute(f"SELECT DISTINCT quota_code FROM {table_name} WHERE quota_code IS NOT NULL ORDER BY quota_code")
    quotas = [r["quota_code"] for r in cur.fetchall()]
    cur.execute(f"SELECT DISTINCT gender FROM {table_name} WHERE gender IS NOT NULL ORDER BY gender")
    genders = [r["gender"] for r in cur.fetchall()]
    cur.close()
    conn.close()

    return jsonify({
        "years": years, "categories": categories, "rounds": rounds,
        "seat_types": seat_types, "quotas": quotas, "genders": genders,
    })


@medical_predictor_bp.route("/medical-predictor/districts", methods=["GET"])
def get_districts():
    course_slug = request.args.get("course_slug", "mbbs")
    table_name = _get_table_name(course_slug)
    if not table_name:
        return jsonify([])
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"SELECT DISTINCT district FROM {table_name} WHERE district IS NOT NULL ORDER BY district")
    districts = [r["district"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(districts)


@medical_predictor_bp.route("/medical-predictor/colleges", methods=["GET"])
def get_colleges():
    course_slug = request.args.get("course_slug", "mbbs")
    table_name = _get_table_name(course_slug)
    if not table_name:
        return jsonify([])
    districts = [d.strip() for d in (request.args.get("districts") or "").split(",") if d.strip()]

    conn = get_connection()
    cur = get_cursor(conn)
    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        cur.execute(
            f"SELECT DISTINCT college_name AS name FROM {table_name} "
            f"WHERE district IN ({placeholders}) ORDER BY college_name",
            tuple(districts)
        )
    else:
        cur.execute(f"SELECT DISTINCT college_name AS name FROM {table_name} ORDER BY college_name")
    colleges = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(colleges)


# ─── Stats (admin panel) ─────────────────────────────────────────────────
@medical_predictor_bp.route("/medical-predictor/stats", methods=["GET"])
def stats():
    course_slug = request.args.get("course_slug", "mbbs")
    table_name = _get_table_name(course_slug)
    if not table_name:
        return jsonify({"total_records": 0, "years": [], "categories": []})

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"SELECT COUNT(*) AS total FROM {table_name}")
    total = cur.fetchone()["total"]
    cur.execute(f"SELECT DISTINCT cap_year FROM {table_name} WHERE cap_year IS NOT NULL ORDER BY cap_year")
    years = [r["cap_year"] for r in cur.fetchall()]
    cur.execute(f"SELECT DISTINCT category FROM {table_name} WHERE category IS NOT NULL")
    categories = [r["category"] for r in cur.fetchall()]
    cur.close()
    conn.close()

    return jsonify({"total_records": total, "years": years, "categories": categories})


# ─── Predict — NEET Marks / NEET Rank based (core of this module) ────────
@medical_predictor_bp.route("/medical-predictor/predict", methods=["POST"])
def predict():
    data = request.get_json(silent=True) or {}
    course_slug = data.get("course_slug", "mbbs")
    table_name = _get_table_name(course_slug)
    if not table_name:
        return jsonify({"error": f"Unknown or inactive medical course '{course_slug}'"}), 400

    neet_rank = data.get("neet_rank")
    neet_marks = data.get("neet_marks")
    if not neet_rank and not neet_marks:
        return jsonify({"error": "Provide NEET Rank or NEET Marks"}), 400

    category = data.get("category", "")
    cap_year = data.get("cap_year", "")
    cap_round = data.get("cap_round")  # list or "All Rounds"
    districts = data.get("districts", []) or []
    colleges = data.get("colleges", []) or []
    seat_types = data.get("seat_types", []) or []
    quotas = data.get("quotas", []) or []
    gender = data.get("gender", "")

    where = ["1=1"]
    params = []

    if category:
        where.append("category = %s")
        params.append(category)
    if cap_year:
        where.append("cap_year = %s")
        params.append(cap_year)
    if cap_round and cap_round != "All Rounds" and isinstance(cap_round, list) and len(cap_round):
        placeholders = ",".join(["%s"] * len(cap_round))
        where.append(f"cap_round IN ({placeholders})")
        params.extend(cap_round)
    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        where.append(f"district IN ({placeholders})")
        params.extend(districts)
    if colleges:
        placeholders = ",".join(["%s"] * len(colleges))
        where.append(f"college_name IN ({placeholders})")
        params.extend(colleges)
    if seat_types:
        placeholders = ",".join(["%s"] * len(seat_types))
        where.append(f"seat_type IN ({placeholders})")
        params.extend(seat_types)
    if quotas:
        placeholders = ",".join(["%s"] * len(quotas))
        where.append(f"quota_code IN ({placeholders})")
        params.extend(quotas)
    if gender:
        where.append("gender = %s")
        params.append(gender)

    # Core NEET logic: a student is a realistic match at a college/round
    # if their NEET rank is not drastically worse than the last-admitted
    # (cutoff) rank there. We widen the window generously (up to 40% worse
    # than cutoff) so "Dream" colleges still show up, then rank/label
    # everything client-side-friendly via probability.
    if neet_rank:
        try:
            neet_rank_int = int(neet_rank)
            where.append("neet_rank_cutoff IS NOT NULL AND neet_rank_cutoff >= %s * 0.6")
            params.append(neet_rank_int)
        except (TypeError, ValueError):
            neet_rank_int = None
    else:
        neet_rank_int = None

    query = f"SELECT * FROM {table_name} WHERE {' AND '.join(where)} ORDER BY neet_rank_cutoff ASC"

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(query, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    results = []
    for r in rows:
        cutoff_rank = r.get("neet_rank_cutoff")
        prob = _calc_probability(neet_rank_int, cutoff_rank)
        diff_ratio = 0
        if neet_rank_int and cutoff_rank:
            diff_ratio = (cutoff_rank - neet_rank_int) / max(cutoff_rank, 1)
        r["probability_pct"] = prob["pct"]
        r["probability_label"] = prob["label"]
        r["chance"] = _chance_label(diff_ratio)
        r["id"] = r.get("id")
        results.append(r)

    return jsonify({
        "total": len(results),
        "student_neet_rank": neet_rank_int,
        "student_neet_marks": neet_marks,
        "results": results,
    })


# ─── PDF download (basic — NEET-oriented report) ─────────────────────────
@medical_predictor_bp.route("/medical-predictor/download-pdf", methods=["POST"])
def download_pdf():
    data = request.get_json(silent=True) or {}
    student = data.get("student", {}) or {}
    results = data.get("results", []) or []

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        return jsonify({"error": "reportlab is not installed on the server."}), 500

    try:
        buffer = io.BytesIO()
        styles = getSampleStyleSheet()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
        elements = []

        title = f"{student.get('name', 'Student')} — Medical Admission Prediction Report"
        elements.append(Paragraph(title, styles["Title"]))
        subtitle = f"NEET Rank: {student.get('neet_rank', '-')}  |  NEET Marks: {student.get('neet_marks', '-')}  |  Category: {student.get('category', '-')}"
        elements.append(Paragraph(subtitle, styles["Normal"]))
        elements.append(Spacer(1, 12))

        table_data = [["Sr.", "College", "Course", "Seat Type", "Quota", "NEET Rank Cutoff", "Fees (₹)", "Chance"]]
        for i, r in enumerate(results, 1):
            table_data.append([
                str(i),
                r.get("college_name", "-"),
                r.get("course_name", "-"),
                r.get("seat_type", "-"),
                r.get("quota_code", "-"),
                str(r.get("neet_rank_cutoff", "-")),
                str(r.get("fees", "-")),
                r.get("chance", "-"),
            ])

        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565c0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8faff")]),
        ]))
        elements.append(t)
        doc.build(elements)
        buffer.seek(0)

        return send_file(
            buffer, mimetype="application/pdf", as_attachment=True,
            download_name="Medical_Admission_Prediction.pdf"
        )
    except Exception as e:
        logger.exception("[medical:download_pdf] failed")
        return jsonify({"error": str(e)}), 500


# ─── Admin: clear all data for a medical course (dangerous, password-protected) ──
@medical_predictor_bp.route("/medical-predictor/clear", methods=["DELETE"])
def clear_data():
    data = request.get_json(silent=True) or {}
    err = _check_admin(data)
    if err:
        return err

    course_slug = request.args.get("course_slug") or data.get("course_slug", "mbbs")
    table_name = _get_table_name(course_slug)
    if not table_name:
        return jsonify({"error": f"Unknown or inactive medical course '{course_slug}'"}), 400

    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(f"DELETE FROM {table_name}")
        conn.commit()
        return jsonify({"message": f"All data cleared from {table_name}"})
    except Exception as e:
        conn.rollback()
        logger.exception("[medical:clear_data] failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()