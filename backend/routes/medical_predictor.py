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


# ─── Rank-based & Marks-based probability (two SEPARATE calculations) ───
# Rank: a LOWER NEET rank is BETTER — closing rank ABOVE the student's rank
#   (i.e. numerically larger/worse) means the college is comfortably
#   reachable => Very High. Closing rank BELOW the student's rank means the
#   college needed a better rank than the student has => chance drops the
#   further away that gets. Near-equal cutoff/rank => High (borderline).
# Marks: a HIGHER NEET score is BETTER — the mirror image of rank. Cutoff
#   marks BELOW the student's marks => easily cleared => Very High. Cutoff
#   marks ABOVE the student's marks => harder to clear => chance drops the
#   further away that gets.
# Both share the same bucket thresholds; only the sign of diff_ratio differs
# depending on which direction is "better" for that metric.
def _prob_from_ratio(diff_ratio):
    if diff_ratio >= 0.20:  return {"pct": 99, "label": "Very High"}
    if diff_ratio >= 0.05:  return {"pct": 92, "label": "Very High"}
    if diff_ratio >= -0.05: return {"pct": 78, "label": "High"}
    if diff_ratio >= -0.15: return {"pct": 55, "label": "Medium"}
    if diff_ratio >= -0.30: return {"pct": 30, "label": "Low"}
    if diff_ratio >= -0.50: return {"pct": 12, "label": "Very Low"}
    return {"pct": 0, "label": "Out of Range"}


def _calc_rank_probability(student_rank, cutoff_rank):
    # No cutoff data for this college => don't invent a probability.
    if student_rank is None or cutoff_rank is None:
        return {"pct": 0, "label": "No Data"}
    try:
        student_rank = int(student_rank)
        cutoff_rank = int(cutoff_rank)
    except (TypeError, ValueError):
        return {"pct": 0, "label": "No Data"}
    if cutoff_rank <= 0:
        return {"pct": 0, "label": "No Data"}
    # Positive diff = student's rank is BETTER (lower number) than the
    # last-admitted rank at this college/category last year.
    return _prob_from_ratio((cutoff_rank - student_rank) / cutoff_rank)


def _calc_marks_probability(student_marks, cutoff_marks):
    if student_marks is None or cutoff_marks is None:
        return {"pct": 0, "label": "No Data"}
    try:
        student_marks = float(student_marks)
        cutoff_marks = float(cutoff_marks)
    except (TypeError, ValueError):
        return {"pct": 0, "label": "No Data"}
    if cutoff_marks <= 0:
        return {"pct": 0, "label": "No Data"}
    # Positive diff = student's marks are HIGHER (better) than the cutoff.
    return _prob_from_ratio((student_marks - cutoff_marks) / cutoff_marks)


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

    # Column alias mapping — handles different Excel/CSV header naming
    # conventions (e.g. "Institute Name" instead of "College Name", "Year"
    # instead of "CAP Year", "Rank"/"Percentile" instead of the DB's
    # neet_rank_cutoff/neet_marks_cutoff column names) so uploads don't
    # fail just because the source sheet uses different column titles.
    COLUMN_ALIASES = {
        "institute_name": "college_name",
        "inst_name": "college_name",
        "institute_code": "college_code",
        "inst_code": "college_code",
        "year": "cap_year",
        "rank": "neet_rank_cutoff",
        "percentile": "neet_marks_cutoff",
        "marks": "neet_marks_cutoff",
        "admission_autherity": "admission_authority",  # common typo in source sheets
        "course": "course_name",
        "quota": "quota_code",
        "branch": "course_name",
    }
    df = df.rename(columns={k: v for k, v in COLUMN_ALIASES.items() if k in df.columns})

    # Drop duplicate columns keeping last (renamed ones take priority)
    df = df.loc[:, ~df.columns.duplicated(keep="last")]

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
                    college_code       = EXCLUDED.college_code,
                    neet_marks_cutoff  = EXCLUDED.neet_marks_cutoff,
                    neet_rank_cutoff   = EXCLUDED.neet_rank_cutoff,
                    fees               = EXCLUDED.fees,
                    university         = EXCLUDED.university,
                    district           = EXCLUDED.district,
                    location           = EXCLUDED.location,
                    address            = EXCLUDED.address,
                    naac_grade         = EXCLUDED.naac_grade,
                    nba_accredited     = EXCLUDED.nba_accredited,
                    website            = EXCLUDED.website,
                    admission_authority = EXCLUDED.admission_authority,
                    is_autonomous      = EXCLUDED.is_autonomous,
                    updated_at         = CURRENT_TIMESTAMP
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
# Rank-Based and Marks-Based prediction are two SEPARATE calculations
# (never mixed into one probability number — see requirement doc). Rank
# takes priority when both are supplied since NEET counselling itself runs
# on rank; marks-only mode kicks in only when no rank is entered.
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

    mode = "rank" if neet_rank else "marks"

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

    neet_rank_int = None
    neet_marks_val = None
    if mode == "rank":
        try:
            neet_rank_int = int(neet_rank)
        except (TypeError, ValueError):
            return jsonify({"error": "NEET Rank must be a number"}), 400
        order_by = "neet_rank_cutoff ASC NULLS LAST"
    else:
        try:
            neet_marks_val = float(neet_marks)
        except (TypeError, ValueError):
            return jsonify({"error": "NEET Marks must be a number"}), 400
        order_by = "neet_marks_cutoff DESC NULLS LAST"

    # IMPORTANT: we no longer exclude rows here just because a cutoff looks
    # "too tough" or "too easy" for this rank/marks. Every college matching
    # the selected admission parameters (category, quota, round, etc.) is
    # returned — the *Probability Chance* computed below is what tells the
    # student how realistic each one is. Rows with no cutoff value, or that
    # fall outside the range this filtered dataset has ever admitted, get
    # Probability Chance = 0% instead of being silently dropped.
    query = f"SELECT * FROM {table_name} WHERE {' AND '.join(where)} ORDER BY {order_by}"

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(query, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    # ── Dataset-level out-of-range check (requirement #3) ──
    # If the student's rank/marks falls outside the range this filtered
    # dataset has EVER admitted, there is no cutoff evidence to support any
    # probability at all, so every result in this set is forced to 0%.
    dataset_out_of_range = False
    if mode == "rank":
        valid_cutoffs = [r["neet_rank_cutoff"] for r in rows if r.get("neet_rank_cutoff") is not None]
        if valid_cutoffs and neet_rank_int > max(valid_cutoffs):
            dataset_out_of_range = True
    else:
        valid_cutoffs = [r["neet_marks_cutoff"] for r in rows if r.get("neet_marks_cutoff") is not None]
        if valid_cutoffs and neet_marks_val < min(float(v) for v in valid_cutoffs):
            dataset_out_of_range = True

    results = []
    for r in rows:
        cutoff_rank = r.get("neet_rank_cutoff")
        cutoff_marks = r.get("neet_marks_cutoff")

        if mode == "rank":
            prob = _calc_rank_probability(neet_rank_int, cutoff_rank)
            diff_ratio = ((cutoff_rank - neet_rank_int) / cutoff_rank) if cutoff_rank else 0
        else:
            prob = _calc_marks_probability(neet_marks_val, cutoff_marks)
            diff_ratio = ((neet_marks_val - float(cutoff_marks)) / float(cutoff_marks)) if cutoff_marks else 0

        if dataset_out_of_range:
            prob = {"pct": 0, "label": "Out of Range"}

        r["probability_pct"] = prob["pct"]
        r["probability_label"] = prob["label"]
        r["chance"] = _chance_label(diff_ratio) if prob["pct"] > 0 else "Dream"
        r["prediction_mode"] = mode
        r["id"] = r.get("id")
        results.append(r)

    return jsonify({
        "total": len(results),
        "mode": mode,
        "student_neet_rank": neet_rank_int,
        "student_neet_marks": (float(neet_marks) if neet_marks not in (None, "") else None),
        "results": results,
    })


# ─── PDF download — same visual design as College Predictor's PDF ────────
# (logo header, green tagline bar, blue contact bar, styled table, green
# footer, counsellor notes) but with MEDICAL columns/logic: NEET Rank
# Cutoff instead of Percentile Cut-off, Seat Type + Quota instead of
# Branch/University, and the rank-based probability already used by
# this module's /predict endpoint (lower rank = better).
@medical_predictor_bp.route("/medical-predictor/download-pdf", methods=["POST"])
def download_pdf():
    data = request.get_json(silent=True) or {}
    student = data.get("student", {}) or {}
    results = data.get("results", []) or []

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image as RLImage
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return jsonify({"error": "reportlab is not installed on the server."}), 500

    def _fmt_number(val):
        if val is None or val == "":
            return "-"
        try:
            return f"{int(float(str(val).replace(',', '').replace('Rs.', '').strip())):,}"
        except Exception:
            return str(val).strip() or "-"

    try:
        buffer = io.BytesIO()
        styles = getSampleStyleSheet()
        elements = []

        # Medical report always uses landscape width — table has 9 columns
        # so it reads better wide, matching the Engineering PDF's
        # auto-widen-when-many-columns behaviour.
        PAGE_WIDTH_MM = 297
        CONTENT_WIDTH_MM = PAGE_WIDTH_MM - 20

        DEJAVU_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.path.exists(DEJAVU_PATH):
            pdfmetrics.registerFont(TTFont("DejaVuSans", DEJAVU_PATH))
            FEE_FONT = "DejaVuSans"
        else:
            FEE_FONT = "Helvetica"

        # ── HEADER — Logo (centered) ──────────────────────────
        LOGO_PATH = "/home/anuradha/Careermyntra_Portal/frontend/images/logo.jpeg"
        if os.path.exists(LOGO_PATH):
            logo_element = RLImage(LOGO_PATH, width=60 * mm, height=26 * mm, kind="proportional")
        else:
            logger.warning(f"[medical:download_pdf] Logo not found at {LOGO_PATH}")
            logo_element = Paragraph(
                "<b><font color='#1565c0' size=16>Career</font><font color='#16a34a' size=16>Myntra</font></b>",
                ParagraphStyle("logo", parent=styles["Normal"], fontSize=16, leading=20, alignment=TA_CENTER)
            )
        logo_tbl = Table([[logo_element]], colWidths=[CONTENT_WIDTH_MM * mm])
        logo_tbl.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(logo_tbl)

        # ── Green tagline bar ─────────────────────────────────
        tagline_tbl = Table([["Aptitude Test  |  Mock Exams  |  Admission Guidance  |  Skills Dev.  |  Jobs"]],
                             colWidths=[CONTENT_WIDTH_MM * mm])
        tagline_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#16a34a")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(tagline_tbl)

        # ── Blue contact bar ──────────────────────────────────
        col_w = CONTENT_WIDTH_MM / 3
        contact_tbl = Table([[
            Paragraph("Phone: +91 98609 38338", ParagraphStyle("c1", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph("Email: info@careermyntra.com", ParagraphStyle("c2", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph("Web: https://careermyntra.com", ParagraphStyle("c3", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)),
        ]], colWidths=[col_w * mm, col_w * mm, col_w * mm])
        contact_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1565c0")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(contact_tbl)
        elements.append(Spacer(1, 8))

        # ── STUDENT INFO ───────────────────────────────────────
        name = student.get("name") or "Student"
        category = student.get("category") or ""
        neet_rank = student.get("neet_rank") or ""
        neet_marks = student.get("neet_marks") or ""

        info_style = ParagraphStyle("info", parent=styles["Normal"], fontSize=10,
                                     textColor=colors.HexColor("#0d1b3e"), leading=16)
        elements.append(Paragraph(f"<b>Full Name:</b> {name}", info_style))
        if category:
            elements.append(Paragraph(f"<b>Category:</b> {category}", info_style))
        if neet_rank:
            elements.append(Paragraph(f"<b>NEET Rank:</b> {neet_rank}", info_style))
        if neet_marks:
            elements.append(Paragraph(f"<b>NEET Marks:</b> {neet_marks}", info_style))
        elements.append(Spacer(1, 10))

        # ── TABLE TITLE ─────────────────────────────────────────
        title_style = ParagraphStyle("title", parent=styles["Normal"], fontSize=13,
                                      fontName="Helvetica-Bold", alignment=TA_CENTER,
                                      textColor=colors.HexColor("#0d1b3e"), spaceAfter=6)
        elements.append(Paragraph("Medical College Prediction List", title_style))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0d1b3e")))
        elements.append(Spacer(1, 4))

        # ── Doc (landscape) ──────────────────────────────────────
        doc = SimpleDocTemplate(
            buffer, pagesize=landscape(A4),
            topMargin=12 * mm, bottomMargin=12 * mm,
            leftMargin=10 * mm, rightMargin=10 * mm
        )

        # Column ON/OFF toggles (requirement #5) — sent by the frontend as
        # data.columns = {rank, marks, status, fees, probability}. College
        # Code - College Name (+ CAP Round beneath it) is always shown and
        # is not one of the toggle-able columns.
        col_toggles = data.get("columns") or {}
        show_status = col_toggles.get("status", True)
        show_rank = col_toggles.get("rank", True)
        show_marks = col_toggles.get("marks", True)
        show_fees = col_toggles.get("fees", True)
        show_probability = col_toggles.get("probability", True)

        col_headers = ["Sr.", "College Code - College Name"]
        raw_widths = [8, 62]
        if show_status:
            col_headers.append("Status"); raw_widths.append(16)
        if show_rank:
            col_headers.append("Rank"); raw_widths.append(20)
        if show_marks:
            col_headers.append("Marks"); raw_widths.append(18)
        if show_fees:
            col_headers.append("Fees (₹)"); raw_widths.append(20)
        if show_probability:
            col_headers.append("Probability Chance (%)"); raw_widths.append(26)

        available_width_mm = 297 - 20
        total_raw = sum(raw_widths)
        scale = available_width_mm / total_raw if total_raw > available_width_mm else 1.0
        col_widths = [w * scale * mm for w in raw_widths]

        table_data = [col_headers]

        for i, r in enumerate(results, start=1):
            fees = _fmt_number(r.get("fees"))
            fees_str = Paragraph(
                f"₹{fees} / year" if fees != "-" else "—",
                ParagraphStyle("fee", fontSize=7, leading=9, fontName=FEE_FONT, alignment=1)
            )

            chance = r.get("chance") or "Dream"
            prob_pct = r.get("probability_pct")
            prob_label = r.get("probability_label") or chance
            prob_str = f"{prob_pct}% {prob_label}" if prob_pct is not None else prob_label

            college_code = str(r.get("college_code") or "").strip()
            college_name_val = str(r.get("college_name") or "—")
            code_name_label = f"{college_code} - {college_name_val}" if college_code else college_name_val
            cap_round_val = str(r.get("cap_round") or "").strip()
            cap_round_line = (
                f'<br/><font size="7" color="#6b7280">CAP Round: {cap_round_val}</font>'
                if cap_round_val else ""
            )
            college_para = Paragraph(
                f"{code_name_label}{cap_round_line}",
                ParagraphStyle("cn", fontSize=8, leading=10)
            )

            row = [str(i), college_para]
            if show_status:
                row.append(str(chance))
            if show_rank:
                row.append(_fmt_number(r.get("neet_rank_cutoff")))
            if show_marks:
                row.append(_fmt_number(r.get("neet_marks_cutoff")))
            if show_fees:
                row.append(fees_str)
            if show_probability:
                row.append(prob_str)
            table_data.append(row)

        tbl = Table(table_data, repeatRows=1, colWidths=col_widths)

        row_styles = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565c0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (1, 1), (1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#0d47a1")),
        ]
        for row_idx in range(1, len(table_data)):
            bg = colors.white if row_idx % 2 == 0 else colors.HexColor("#f0fdf4")
            row_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))
        row_styles.append(("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor("#16a34a")))
        tbl.setStyle(TableStyle(row_styles))
        elements.append(tbl)
        elements.append(Spacer(1, 12))

        # ── COUNSELLOR NOTE ────────────────────────────────────
        note_title = ParagraphStyle("nt", parent=styles["Normal"], fontSize=9,
                                     fontName="Helvetica-Bold", textColor=colors.HexColor("#0d1b3e"),
                                     spaceAfter=4)
        note_body = ParagraphStyle("nb", parent=styles["Normal"], fontSize=8,
                                    textColor=colors.HexColor("#374151"), leading=13)
        elements.append(Paragraph("Counsellor's Note", note_title))
        notes = [
            "This list is a <b>prediction</b> based on your NEET rank/marks and is <b>not an official CAP allotment or admission list</b>.",
            "The predictions are prepared using <b>previous CAP/counselling cut-offs, your category, rank, seat availability, and other admission parameters</b>.",
            "The <b>Chance (%)</b> indicates the likelihood of admission. It does <b>not guarantee admission</b>.",
            "The <b>fees shown are approximate annual tuition fees</b>. Actual fees may vary by seat type and quota.",
            "Cut-offs may change every year based on applicants, seat availability, and reservation policies.",
            "We recommend a <b>balanced mix of Dream, Moderate, and Safe colleges</b> in your option form.",
            "Before confirming admission, verify latest fee structure and eligibility from the respective institute.",
            "For the best outcome, <b>consult your counsellor</b> before finalizing your option form.",
        ]
        for idx, note in enumerate(notes, 1):
            elements.append(Paragraph(f"{idx}. {note}", note_body))
        elements.append(Spacer(1, 10))

        # ── GREEN FOOTER ────────────────────────────────────────
        footer_tbl = Table([["Sunny Pride, JM Road, Z Bridge, Deccan Gymkhana, Pune, Maharashtra 411004"]],
                            colWidths=[277 * mm])
        footer_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#16a34a")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        elements.append(footer_tbl)

        doc.build(elements)
        buffer.seek(0)

        safe_name = "".join(c for c in name if c.isalnum() or c in " _-").strip().replace(" ", "_") or "Student"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"{safe_name}_Medical_Prediction.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        logger.exception("[medical:download_pdf] PDF generation failed")
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500


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