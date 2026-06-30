# routes/college_predictor.py — College Predictor Blueprint
# Handles: CAP cutoff CSV/Excel upload (admin), prediction API, district list

from flask import Blueprint, request, jsonify, send_file
import os
import io
import pandas as pd
from datetime import datetime
from db import get_connection, get_cursor
from logger_setup import get_logger

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)
from reportlab.lib.enums import TA_LEFT

logger = get_logger(__name__)
college_predictor_bp = Blueprint("college_predictor", __name__)

# ─── Helper: ±5 percentile range — all results are "Probable" ─────────
def _chance_label(student_percentile, cutoff_percentile):
    return "Probable"

CHANCE_ORDER = {"Probable": 0}

# ─── Helper functions for Excel column mapping ───────────────

GENDER_MAP = {
    'G': 'All',    # General (All genders)
    'L': 'Female', # Ladies
    'P': 'All',    # Persons with Disability
    'D': 'All',    # Defence
    'T': 'All',    # Tribal
    'O': 'Other',  # Others
    'E': 'All',    # Ex-serviceman
    'M': 'Male',   # Male
}

QUOTA_MAP = {
    'S': 'State',
    'H': 'Home University',
    'O': 'Outside Home University',
    'N': 'NRI',
    'I': 'Institute Level',
}

# University → Districts mapping (Maharashtra)
UNIVERSITY_DISTRICT_MAP = {
    "Savitribai Phule Pune University": [
        "Pune", "Nashik", "Ahmednagar", "Ahilyanagar"
    ],
    "Mumbai University": [
        "Mumbai", "Thane", "Raigad", "Ratnagiri", "Sindhudurg", "Palghar", "Konkan"
    ],
    "Shivaji University": [
        "Kolhapur", "Sangli", "Satara", "Solapur"
    ],
    "Dr. Babasaheb Ambedkar Marathwada University": [
        "Aurangabad", "Chhatrapati Sambhajinagar", "Jalna", "Beed", "Bid"
    ],
    "Swami Ramanand Teerth Marathwada University, Nanded": [
        "Nanded", "Latur", "Osmanabad", "Dharashiv", "Hingoli", "Parbhani"
    ],
    "Rashtrasant Tukadoji Maharaj Nagpur University": [
        "Nagpur", "Wardha", "Chandrapur", "Gadchiroli", "Gondia", "Bhandara"
    ],
    "Sant Gadge Baba Amravati University": [
        "Amravati", "Akola", "Washim", "Buldhana", "Yavatmal"
    ],
    "Kavayitri Bahinabai Chaudhari North Maharashtra University, Jalgaon": [
        "Jalgaon", "Dhule", "Nandurbar"
    ],
    "Gondwana University": [
        "Gadchiroli", "Gondia", "Chandrapur"
    ],
    "Punyashlok Ahilyadevi Holkar Solapur University": [
        "Solapur"
    ],
    "Dr. Babasaheb Ambedkar Technological University,Lonere": [
        "Raigad", "Ratnagiri", "Sindhudurg"
    ],
    "SNDT Women's University": [
        "Mumbai", "Pune"
    ],
}

def _get_home_university(home_district):
    """Given student's home district, return their home university name"""
    if not home_district:
        return None
    hd = str(home_district).strip().lower()
    for univ, districts in UNIVERSITY_DISTRICT_MAP.items():
        if any(d.lower() == hd or d.lower() in hd or hd in d.lower() for d in districts):
            return univ
    return None

def _get_applicable_quota(college_university, home_university, is_autonomous):
    """
    Returns list of applicable quota labels for this student+college combination.
    Rules:
    - Autonomous Institute → State Quota only
    - Home University match → State + Home University
    - Otherwise → State + Outside Home University
    """
    if is_autonomous or not college_university or college_university == "Autonomous Institute":
        return ["State"]
    if not home_university:
        return ["State"]
    if college_university.strip().lower() == home_university.strip().lower():
        return ["State", "Home University"]
    return ["State", "Outside Home University"]

def _map_gender(g):
    if not g: return 'All'
    return GENDER_MAP.get(str(g).strip().upper(), str(g).strip())

def _map_quota(q):
    if not q: return 'AI'
    return QUOTA_MAP.get(str(q).strip().upper(), str(q).strip())

def _check_autonomous(status_val):
    if not status_val: return False
    s = str(status_val).lower()
    return 'autonomous institute' in s or s in ('yes', 'true', '1', 'y')

def _extract_university(row):
    """Extract university from 'university' column or from 'status_full' column"""
    u = row.get("university")
    if u and str(u).strip() and str(u).strip().lower() not in ('nan', 'none', ''):
        # If status says 'Autonomous Institute', return that
        status = str(row.get("status_full", "")).strip()
        if "autonomous institute" in status.lower():
            return "Autonomous Institute"
        return str(u).strip()
    # Try extracting from status_full — format: "Type Home University : University Name"
    status = str(row.get("status_full", "")).strip()
    if ":" in status:
        return status.split(":")[-1].strip()
    return None

def _format_cap_year(y):
    """Convert 2025 → '2025-26'"""
    if not y: return None
    try:
        yr = int(float(str(y).strip()))
        return f"{yr}-{str(yr+1)[-2:]}"
    except:
        return str(y).strip()

def _format_cap_round(r):
    """Convert 1 → 'Round I', 2 → 'Round II' etc."""
    if not r: return 'Round I'
    try:
        n = int(float(str(r).strip()))
        roman = {1:'I', 2:'II', 3:'III', 4:'IV', 5:'V'}
        return f"Round {roman.get(n, str(n))}"
    except:
        return str(r).strip()


# ─── 1. Admin: Upload CAP Cutoff CSV / Excel ────────────────
@college_predictor_bp.route("/college-predictor/upload-cutoff", methods=["POST"])
def upload_cutoff():
    """
    Admin uploads a CSV or Excel file with CAP cutoff data.
    Expected columns (case-insensitive):
      college_code, college_name, branch_name, district, university,
      cap_year, cap_round, category, seat_type, exam_type,
      cutoff_percentile, cutoff_score, fees, naac_grade, nba_accredited,
      placement_highest, placement_average, website, address
    """
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    filename = file.filename.lower()
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(file)
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file)
        else:
            return jsonify({"error": "Only CSV or Excel (.xlsx/.xls) allowed"}), 400
    except Exception as e:
        return jsonify({"error": f"File read error: {str(e)}"}), 400

    # Normalize column names
    # Normalize column names but preserve category(1)
    new_cols = {}
    for c in df.columns:
        normalized = c.strip().lower().replace(" ", "_")
        new_cols[c] = normalized
    df = df.rename(columns=new_cols)

    # Special fix: category(1) → category_1_ after normalize — rename to category_simple
    if 'category(1)' in df.columns:
        df = df.rename(columns={'category(1)': 'category_simple'})
    # Also handle post-normalization name
    for col in df.columns:
        if '(1)' in col or col in ('category_1_', 'category_(1)', 'category(1)'):
            df = df.rename(columns={col: 'category_simple'})
            break

    # ── Column alias mapping — handle different CSV formats ──
    ALIASES = {
        "institute_code": "college_code", "inst_code": "college_code",
        "institute_name": "college_name", "inst_name": "college_name",
        # Direct column renames
        "institute name": "college_name",
        "institute code": "college_code",
        "branch": "branch_name",
        "branch code": "branch_code",
        "course": "course_name",
        "university": "university",
        "category_simple": "category",    # Simplified category (OPEN, SC, ST...)
        "sub_category": "category_full",  # Full code (GOPENS, GSCS...) — keep for reference
        "gender": "gender_code",          # G, L, P, D, T, O, E, M
        "quota": "quota_code",            # S, H, N, I, O
        "percentile": "cutoff_percentile",
        "rank": "cutoff_score",
        "year": "cap_year",
        "cap round": "cap_round",
        "district": "district",
        "address": "address",
        "pincode": "pincode",
        "status": "status_full",          # Full status string
        # Also handle already-mapped names
        "course_name": "course_name",
        "branch_name": "branch_name",
        "is_autonomous": "is_autonomous",
        "category(1)": "category",
        "percentile": "cutoff_percentile", "cutoff": "cutoff_percentile",
        "year": "cap_year",
        "cap_round": "cap_round", "round": "cap_round",
        "rank": "cutoff_score",
        "quota": "seat_type",
    }
    df.rename(columns={k: v for k, v in ALIASES.items() if k in df.columns}, inplace=True)

    # Drop duplicate columns keeping last (renamed ones take priority)
    df = df.loc[:, ~df.columns.duplicated(keep="last")]

    # Format cap_round: 1 -> "Round I"
    if "cap_round" in df.columns:
        roman = {1:"Round I",2:"Round II",3:"Round III",4:"Round IV",5:"Round V"}
        df["cap_round"] = df["cap_round"].apply(lambda v: roman.get(int(float(str(v))), f"Round {v}") if str(v).strip().isdigit() else (str(v) if v else "Round I"))

    # Format cap_year: 2025 -> "2025-26"
    if "cap_year" in df.columns:
        def fmt_year(v):
            try:
                s = str(v).strip()
                if "-" in s: return s
                y = int(float(s))
                return f"{y}-{str(y+1)[2:]}"
            except: return str(v)
        df["cap_year"] = df["cap_year"].apply(fmt_year)

    required = {"college_name", "branch_name", "cap_year", "cap_round",
                "category", "cutoff_percentile"}
    missing = required - set(df.columns)
    if missing:
        return jsonify({"error": f"Missing required columns: {', '.join(missing)}"}), 400

    # Replace NaN with None for DB
    df = df.where(pd.notnull(df), None)

    conn = get_connection()
    cur = conn.cursor()
    inserted = 0
    skipped = 0

    for _, row in df.iterrows():
        try:
            cur.execute("""
                INSERT INTO cap_cutoff_data (
                    college_code, college_name, branch_name, district, university,
                    cap_year, cap_round, category, seat_type, exam_type,
                    cutoff_percentile, cutoff_score, fees, naac_grade,
                    nba_accredited, placement_highest, placement_average,
                    website, address,
                    gender, quota_code, is_autonomous, course_name
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s
                )
                ON CONFLICT (college_name, branch_name, cap_year, cap_round, category, seat_type)
                DO UPDATE SET
                    college_code      = EXCLUDED.college_code,
                    district          = EXCLUDED.district,
                    university        = EXCLUDED.university,
                    exam_type         = EXCLUDED.exam_type,
                    cutoff_percentile = EXCLUDED.cutoff_percentile,
                    cutoff_score      = EXCLUDED.cutoff_score,
                    fees              = EXCLUDED.fees,
                    naac_grade        = EXCLUDED.naac_grade,
                    nba_accredited    = EXCLUDED.nba_accredited,
                    placement_highest = EXCLUDED.placement_highest,
                    placement_average = EXCLUDED.placement_average,
                    website           = EXCLUDED.website,
                    address           = EXCLUDED.address,
                    gender            = EXCLUDED.gender,
                    quota_code        = EXCLUDED.quota_code,
                    is_autonomous     = EXCLUDED.is_autonomous,
                    course_name       = EXCLUDED.course_name,
                    updated_at        = CURRENT_TIMESTAMP
            """, (
                row.get("college_code"),
                row.get("college_name"),
                row.get("branch_name"),
                str(row.get("district")).strip() if row.get("district") else None,
                str(row.get("university")).strip() if row.get("university") else None,
                row.get("cap_year"),
                row.get("cap_round"),
                row.get("category") or row.get("category_simple") or row.get("category_full"),
                row.get("seat_type", "AI"),
                row.get("exam_type", "MHT-CET"),
                row.get("cutoff_percentile"),
                row.get("cutoff_score"),
                row.get("fees"),
                row.get("naac_grade"),
                str(row.get("nba_accredited", "No")).strip() if row.get("nba_accredited") else "No",
                row.get("placement_highest"),
                row.get("placement_average"),
                row.get("website"),
                row.get("address"),
                str(row.get("gender_code")).strip().upper() if row.get("gender_code") else None,
                row.get("quota_code") or "S",
                _check_autonomous(row.get("status_full")),
                row.get("course_name"),
            ))
            inserted += 1
        except Exception as e:
            logger.warning(f"[Predictor Upload] Row skipped: {e}")
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()

    logger.info(f"[Predictor Upload] inserted={inserted} skipped={skipped}")
    return jsonify({
        "message": f"Upload complete. {inserted} rows saved, {skipped} skipped.",
        "inserted": inserted,
        "skipped": skipped
    })


# ─── 2. GET /college-predictor/districts — for dropdown ─────
@college_predictor_bp.route("/college-predictor/districts", methods=["GET"])
def get_districts():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT DISTINCT TRIM(district) AS district FROM cap_cutoff_data
        WHERE district IS NOT NULL AND TRIM(district) != ''
        ORDER BY TRIM(district)
    """)
    rows = [r["district"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── NEW: GET /college-predictor/courses — unique course/branch types ──
@college_predictor_bp.route("/college-predictor/courses", methods=["GET"])
def get_courses():
    """Returns distinct branch values from 'branch' column (B.Tech, M.Tech etc)"""
    conn = get_connection()
    cur = get_cursor(conn)
    # 'branch' column in CSV maps to branch_name in DB but contains course type
    # We store it as branch_name — get distinct top-level course names
    cur.execute("""
        SELECT DISTINCT branch_name FROM cap_cutoff_data
        WHERE branch_name IS NOT NULL
        ORDER BY branch_name
    """)
    # Return unique course types — group by first word to get B.Tech, M.Tech etc
    all_branches = [r["branch_name"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    # Extract course type (B.Tech, M.Tech etc) from branch names
    courses = sorted(set(b.split(" - ")[0].strip() if " - " in b else b.split(",")[0].strip() for b in all_branches))
    return jsonify(courses)


# ─── NEW: GET /college-predictor/branches?district=Pune ─────
@college_predictor_bp.route("/college-predictor/branches", methods=["GET"])
def get_branches():
    """Returns distinct branch_name values, optionally filtered by district"""
    district = request.args.get("district", "")
    conn = get_connection()
    cur = get_cursor(conn)
    if district:
        cur.execute("""
            SELECT DISTINCT branch_name FROM cap_cutoff_data
            WHERE district = %s AND branch_name IS NOT NULL
            ORDER BY branch_name
        """, (district,))
    else:
        cur.execute("""
            SELECT DISTINCT branch_name FROM cap_cutoff_data
            WHERE branch_name IS NOT NULL
            ORDER BY branch_name
        """)
    rows = [r["branch_name"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── NEW: GET /college-predictor/filter-options ─────────────
@college_predictor_bp.route("/college-predictor/filter-options", methods=["GET"])
def get_filter_options():
    """Returns all dynamic dropdown values from DB"""
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT DISTINCT cap_year FROM cap_cutoff_data WHERE cap_year IS NOT NULL ORDER BY cap_year DESC")
    years = [r["cap_year"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT cap_round FROM cap_cutoff_data WHERE cap_round IS NOT NULL ORDER BY cap_round")
    rounds = [r["cap_round"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT category FROM cap_cutoff_data WHERE category IS NOT NULL ORDER BY category")
    categories = [r["category"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT exam_type FROM cap_cutoff_data WHERE exam_type IS NOT NULL ORDER BY exam_type")
    exam_types = [r["exam_type"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({
        "years": years,
        "rounds": rounds,
        "categories": categories,
        "exam_types": exam_types,
    })


# ─── 3. POST /college-predictor/predict — main prediction ───
@college_predictor_bp.route("/college-predictor/predict", methods=["POST"])
def predict():
    """
    Body (JSON):
    {
      exam_type: "MHT-CET" | "JEE Main",
      percentile: 88.5,
      category: "OPEN",
      cap_year: "2024-25",
      cap_round: "All Rounds",
      branches: ["Computer Engineering", "Information Technology"],  // optional
      districts: ["Pune", "Nashik"],  // optional, empty = all
      gender: "Male"
    }

    Prediction Logic: ±5 Percentile Range
    - Searches for cutoffs between (percentile - 5) and (percentile + 5)
    - This gives a realistic list of probable admission opportunities
      rather than a guaranteed Safe/Moderate/Dream classification.
    """
    data = request.get_json(silent=True) or {}

    exam_type   = data.get("exam_type", "MHT-CET")
    percentile  = data.get("percentile")
    category    = data.get("category", "OPEN")
    cap_year    = data.get("cap_year", "2024-25")
    cap_round   = data.get("cap_round", "All Rounds")
    branches     = data.get("branches", [])
    districts    = data.get("districts", [])
    home_district = data.get("home_district", "")

    # Normalize frontend display values to DB values
    CATEGORY_MAP = {
        "General (Open)": "OPEN", "general (open)": "OPEN", "open": "OPEN",
        "OBC": "OBC", "SC": "SC", "ST": "ST",
        "NT1": "NT1", "NT2": "NT2", "NT3": "NT3",
        "VJ": "VJ", "EWS": "EWS", "SEBC": "SEBC",
        "PWD": "PWD", "TFWS": "TFWS", "ORPHAN": "ORPHAN",
    }
    category = CATEGORY_MAP.get(category, category.upper() if category else "OPEN")

    CAP_ROUND_MAP = {
        "CAP Round 1": "Round I", "CAP Round 2": "Round II",
        "CAP Round 3": "Round III", "Round 1": "Round I",
        "Round 2": "Round II", "Round 3": "Round III",
        "1": "Round I", "2": "Round II", "3": "Round III",
    }
    cap_round = CAP_ROUND_MAP.get(cap_round, cap_round)

    # cap_year: "2025 (2025-2026)" -> "2025-26"
    if cap_year and "(" in cap_year:
        import re
        m = re.search(r"(\d{4})-(\d{4})", cap_year)
        if m:
            cap_year = f"{m.group(1)}-{m.group(2)[2:]}"

    EXAM_MAP = {
        "MHT CET – Maharashtra Common Entrance Test": "MHT-CET",
        "MHT CET": "MHT-CET", "JEE Main": "JEE Main",
    }
    exam_type = EXAM_MAP.get(exam_type, exam_type)

    if percentile is None:
        return jsonify({"error": "Percentile is required"}), 400

    try:
        percentile = float(percentile)
    except (ValueError, TypeError):
        return jsonify({"error": "Percentile must be a number"}), 400

    # ─── ±5 Percentile Range (Sir's logic) ───
    PERCENTILE_RANGE = 5
    min_percentile = max(0, percentile - PERCENTILE_RANGE)
    max_percentile = min(100, percentile + PERCENTILE_RANGE)

    conn = get_connection()
    cur = get_cursor(conn)

    # Build dynamic query
    where_clauses = [
        "exam_type = %s",
        "category = %s",
        "cap_year = %s",
        "cutoff_percentile BETWEEN %s AND %s",
    ]
    params = [exam_type, category, cap_year, min_percentile, max_percentile]

    # Gender filter — include 'All' records always + gender-specific
    student_gender = data.get("gender", "")
    if student_gender and student_gender != "Other":
        where_clauses.append("(gender = %s OR gender = 'All' OR gender IS NULL)")
        params.append(student_gender)

    if cap_round and cap_round != "All Rounds":
        where_clauses.append("cap_round = %s")
        params.append(cap_round)

    if branches:
        placeholders = ",".join(["%s"] * len(branches))
        where_clauses.append(f"branch_name IN ({placeholders})")
        params.extend(branches)

    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        where_clauses.append(f"district IN ({placeholders})")
        params.extend(districts)

    where_sql = " AND ".join(where_clauses)

    cur.execute(f"""
        SELECT
            id, college_code, college_name, branch_name,
            district, university, cap_year, cap_round,
            category, seat_type, exam_type,
            cutoff_percentile, cutoff_score,
            fees, naac_grade, nba_accredited,
            placement_highest, placement_average,
            website, address,
            gender, quota_code, is_autonomous, course_name
        FROM cap_cutoff_data
        WHERE {where_sql}
        ORDER BY cutoff_percentile DESC
        LIMIT 200
    """, params)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    # Build results — no Safe/Moderate/Dream classification, just probable list
    results = []
    for r in rows:
        results.append({
            "id":                 r["id"],
            "college_code":       r["college_code"],
            "college_name":       r["college_name"],
            "branch_name":        r["branch_name"],
            "district":           r["district"],
            "university":         r["university"],
            "cap_year":           r["cap_year"],
            "cap_round":          r["cap_round"],
            "category":           r["category"],
            "seat_type":          r["seat_type"],
            "exam_type":          r["exam_type"],
            "cutoff_percentile":  float(r["cutoff_percentile"]) if r["cutoff_percentile"] is not None else None,
            "cutoff_score":       r["cutoff_score"],
            "fees":               r["fees"],
            "naac_grade":         r["naac_grade"],
            "nba_accredited":     r["nba_accredited"],
            "placement_highest":  r["placement_highest"],
            "placement_average":  r["placement_average"],
            "website":            r["website"],
            "address":            r["address"],
            "gender_label":       r["gender"] if r["gender"] else "All",
            "quota_code":         r["quota_code"] if r["quota_code"] else "S",
            "is_autonomous":      r["is_autonomous"] if r["is_autonomous"] else False,
            "course_name":        r["course_name"] if r["course_name"] else "",
            "probability":           _chance_label(percentile, r["cutoff_percentile"])
        })

    # Remove duplicate college-branch combinations (keep highest cutoff per combo)
    seen = {}
    for r in results:
        key = (r["college_name"], r["branch_name"], r["category"], r["quota_code"])
        if key not in seen:
            seen[key] = r
    results = list(seen.values())

    # Compute applicable quota for each result based on student's home district
    home_univ = _get_home_university(home_district)
    for r in results:
        r["applicable_quota"] = _get_applicable_quota(
            r.get("university"), home_univ, r.get("is_autonomous", False)
        )
        r["home_university"] = home_univ or ""

    # Sort by cutoff percentile closest to student's percentile first
    results.sort(
    key=lambda x: x["cutoff_percentile"] if x["cutoff_percentile"] is not None else 0,
    reverse=True
)
    
    return jsonify({
        "total": len(results),
        "student_percentile": percentile,
        "percentile_range": {
            "min": round(min_percentile, 2),
            "max": round(max_percentile, 2),
        },
        "results": results
    })


@college_predictor_bp.route("/college-predictor/debug-values", methods=["GET"])
def debug_values():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT DISTINCT cap_year, cap_round, category, exam_type FROM cap_cutoff_data ORDER BY cap_year, cap_round LIMIT 50")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── 4. GET /college-predictor/stats ── admin stats ─────────
@college_predictor_bp.route("/college-predictor/stats", methods=["GET"])
def stats():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT COUNT(*) AS total FROM cap_cutoff_data")
    total = cur.fetchone()["total"]
    cur.execute("SELECT DISTINCT cap_year FROM cap_cutoff_data ORDER BY cap_year DESC")
    years = [r["cap_year"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT category FROM cap_cutoff_data ORDER BY category")
    categories = [r["category"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({"total_records": total, "years": years, "categories": categories})


# ─── 5. DELETE /college-predictor/clear ── admin clear data ─
@college_predictor_bp.route("/college-predictor/clear", methods=["DELETE"])
def clear_data():
    cap_year = request.args.get("cap_year")
    conn = get_connection()
    cur = conn.cursor()
    if cap_year:
        cur.execute("DELETE FROM cap_cutoff_data WHERE cap_year = %s", (cap_year,))
        msg = f"Deleted records for year {cap_year}"
    else:
        cur.execute("DELETE FROM cap_cutoff_data")
        msg = "All cutoff data cleared"
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"message": msg})

# ─── 7. GET /college-predictor/universities ─────────────────
@college_predictor_bp.route("/college-predictor/universities", methods=["GET"])
def get_universities():
    """Returns list of unique universities from database"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)

        cur.execute("""
            SELECT DISTINCT TRIM(university) AS university
            FROM cap_cutoff_data
            WHERE university IS NOT NULL AND TRIM(university) != ''
            ORDER BY TRIM(university)
        """)

        universities = [row["university"] for row in cur.fetchall()]
        cur.close()
        conn.close()

        return jsonify(universities if universities else [])
    except Exception as e:
        logger.error(f"Error fetching universities: {e}")
        return jsonify([]), 500


# ─── 8. GET /college-predictor/colleges ─────────────────
@college_predictor_bp.route("/college-predictor/colleges", methods=["GET"])
def get_colleges():
    """Returns list of unique colleges from database"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        
        cur.execute("""
            SELECT DISTINCT college_name 
            FROM cap_cutoff_data 
            WHERE college_name IS NOT NULL 
            ORDER BY college_name
        """)
        
        colleges = [row["college_name"] for row in cur.fetchall()]
        cur.close()
        conn.close()
        
        return jsonify(colleges if colleges else [])
    except Exception as e:
        logger.error(f"Error fetching colleges: {e}")
        return jsonify([]), 500


# ─── 9. POST /college-predictor/download-pdf ─────────────────
@college_predictor_bp.route("/college-predictor/download-pdf", methods=["POST"])
def download_pdf():
    """Generates CareerMyntra branded College Prediction PDF — exact sample format"""
    try:
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, Image as RLImage
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from flask import send_file

        data     = request.get_json(silent=True) or {}
        student  = data.get("student", {}) or {}
        results  = data.get("results", []) or []

        name       = (student.get("name") or "Student").strip()
        category   = student.get("category") or ""
        percentile = student.get("percentile") or ""
        branches   = student.get("branches") or []
        districts  = student.get("districts") or []
        exam_type  = student.get("exam_type") or "MHT-CET"

        # Logo path
        logo_path = os.path.normpath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "frontend", "images", "logo.jpeg"
        ))

        # ── Colours ──────────────────────────────────────────
        GREEN      = colors.HexColor("#1A7A3E")
        DARK_GREEN = colors.HexColor("#145C2E")
        NAVY       = colors.HexColor("#1B2A5A")
        LIGHT_BG   = colors.HexColor("#F5FFF8")
        BORDER_CLR = colors.HexColor("#B2DFCC")
        TEXT       = colors.HexColor("#111111")
        MUTED      = colors.HexColor("#555555")
        WHITE      = colors.white

        buf = io.BytesIO()
        PAGE_W, PAGE_H = A4
        M = 14 * mm

        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=M, rightMargin=M,
            topMargin=M, bottomMargin=M,
        )
        styles = getSampleStyleSheet()
        story  = []

        def ps(name, **kw):
            return ParagraphStyle(name, **kw)

        # ── 1. TOP HEADER — Logo + Brand + Tagline ────────────
        brand_name = Paragraph(
            "<b>CAREER MYNTRA</b>",
            ps("bn", fontSize=26, fontName="Helvetica-Bold",
               textColor=WHITE, alignment=TA_LEFT, leading=30)
        )
        tagline = Paragraph(
            "Aptitude Test | Mock Exams | Admission Guidance | Skills Dev. | Jobs",
            ps("tg", fontSize=10, fontName="Helvetica",
               textColor=WHITE, alignment=TA_LEFT, leading=14)
        )
        brand_col = Table([[brand_name], [tagline]],
                          colWidths=[130*mm])
        brand_col.setStyle(TableStyle([
            ("TOPPADDING",    (0,0),(-1,-1), 2),
            ("BOTTOMPADDING", (0,0),(-1,-1), 2),
        ]))

        if os.path.exists(logo_path):
            logo_img = RLImage(logo_path, width=22*mm, height=22*mm)
            hdr_data = [[logo_img, brand_col]]
            hdr_cw   = [28*mm, 142*mm]
        else:
            hdr_data = [[brand_col]]
            hdr_cw   = [170*mm]

        hdr = Table(hdr_data, colWidths=hdr_cw)
        hdr.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), GREEN),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("LEFTPADDING",   (0,0),(0,-1),  6),
            ("RIGHTPADDING",  (0,0),(-1,-1), 6),
            ("TOPPADDING",    (0,0),(-1,-1), 8),
            ("BOTTOMPADDING", (0,0),(-1,-1), 8),
        ]))
        story.append(hdr)

        # ── 2. CONTACT STRIP ──────────────────────────────────
        contact = Table([[
            Paragraph("📞 +91 98609 38338",
                      ps("c1", fontSize=9, fontName="Helvetica-Bold",
                         textColor=WHITE, alignment=TA_LEFT)),
            Paragraph("✉ info@careermyntra.com",
                      ps("c2", fontSize=9, fontName="Helvetica",
                         textColor=WHITE, alignment=TA_CENTER)),
            Paragraph("🌐 https://careermyntra.com",
                      ps("c3", fontSize=9, fontName="Helvetica",
                         textColor=WHITE, alignment=TA_RIGHT)),
        ]], colWidths=[57*mm, 57*mm, 56*mm])
        contact.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), NAVY),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 4),
            ("RIGHTPADDING",  (0,0),(-1,-1), 4),
        ]))
        story.append(contact)
        story.append(Spacer(1, 8*mm))

        # ── 3. STUDENT DETAILS ────────────────────────────────
        det = ps("det", fontSize=10.5, fontName="Helvetica",
                 textColor=TEXT, spaceAfter=5, leading=15)

        story.append(Paragraph(f"Full Name: {name}", det))
        story.append(Paragraph(f"Caste Category: {category}", det))
        story.append(Paragraph(f"MHT-CET PCM Percentile: {percentile}", det))
        if branches:
            story.append(Paragraph(f"Preffred Branches :  {', '.join(branches)}", det))
        if districts:
            story.append(Paragraph(f"Preffred City : {', '.join(districts)}", det))
        story.append(Spacer(1, 6*mm))

        # ── 4. TABLE HEADING ──────────────────────────────────
        story.append(Paragraph(
            "<u><b>College Prediction List</b></u>",
            ps("th", fontSize=13, fontName="Helvetica-Bold",
               textColor=TEXT, alignment=TA_CENTER, spaceAfter=6)
        ))

        # ── 5. PROBABILITY LABEL HELPER ───────────────────────
        def prob_label(pct):
            if pct is None: return (15, "Very Low")
            p = int(pct)
            if p >= 95: return (p, "Very High")
            if p >= 80: return (p, "High")
            if p >= 55: return (p, "Medium")
            if p >= 35: return (p, "Low")
            return (p, "Very Low")

        PROB_COLOR = {
            "Very High": colors.HexColor("#15803d"),
            "High":      colors.HexColor("#16a34a"),
            "Medium":    colors.HexColor("#d97706"),
            "Low":       colors.HexColor("#dc2626"),
            "Very Low":  colors.HexColor("#991b1b"),
        }

        # ── 6. STATUS HELPER ──────────────────────────────────
        def status_label(r):
            is_auto = r.get("is_autonomous") or False
            univ    = (r.get("university") or "").strip()
            if "unitary public" in univ.lower():
                return "University Autonomous"
            if is_auto and "un-aided" in univ.lower():
                return "Un-Aided Autonomous"
            if is_auto:
                return "Autonomous"
            if univ:
                return "Un-Aided"
            return "—"

        # ── 7. PREDICTION TABLE ───────────────────────────────
        hs = ps("hs", fontSize=8.5, fontName="Helvetica-Bold",
                textColor=WHITE, alignment=TA_CENTER, leading=11)
        cs = ps("cs", fontSize=8,   fontName="Helvetica",
                textColor=TEXT, leading=10)
        cc = ps("cc", fontSize=8,   fontName="Helvetica",
                textColor=TEXT, alignment=TA_CENTER, leading=10)
        cb = ps("cb", fontSize=8,   fontName="Helvetica-Bold",
                textColor=TEXT, alignment=TA_CENTER, leading=10)

        header_row = [
            Paragraph("Sr.",          hs),
            Paragraph("College Name", hs),
            Paragraph("Branches",     hs),
            Paragraph("Status",       hs),
            Paragraph("District",     hs),
            Paragraph("Cut-off",      hs),
            Paragraph("Rank",         hs),
            Paragraph("Fees (₹)",     hs),
            Paragraph("Probability",  hs),
            Paragraph("Distance",     hs),
        ]

        col_w = [7*mm, 38*mm, 27*mm, 22*mm, 14*mm, 16*mm, 12*mm, 18*mm, 16*mm, 14*mm]
        tdata = [header_row]

        for idx, r in enumerate(results, 1):
            cutoff  = f"{float(r['cutoff_percentile']):.2f} %ile" if r.get("cutoff_percentile") is not None else "—"
            rank_v  = f"{int(float(r['cutoff_score'])):,}" if r.get("cutoff_score") else "—"
            fees_v  = f"₹{int(float(r['fees'])):,} / year" if r.get("fees") else "—"
            prob_v  = r.get("probability")
            p_val, p_lbl = prob_label(prob_v)
            p_clr   = PROB_COLOR.get(p_lbl, MUTED)
            dist_v  = r.get("distance") or "—"
            status  = status_label(r)

            tdata.append([
                Paragraph(str(idx),                      cc),
                Paragraph(str(r.get("college_name","")), cs),
                Paragraph(str(r.get("branch_name","")),  cs),
                Paragraph(status,                        cc),
                Paragraph(r.get("district") or "—",     cc),
                Paragraph(f"<b>{cutoff}</b>",            cb),
                Paragraph(rank_v,                        cc),
                Paragraph(fees_v,                        cc),
                Paragraph(
                    f"<b>{p_val}%</b> {p_lbl}",
                    ps(f"prob{idx}", fontSize=8, fontName="Helvetica",
                       textColor=p_clr, alignment=TA_CENTER, leading=10)
                ),
                Paragraph(dist_v,                        cc),
            ])

        pred_t = Table(tdata, colWidths=col_w, repeatRows=1)
        pred_t.setStyle(TableStyle([
            # Header
            ("BACKGROUND",    (0,0), (-1,0),  GREEN),
            ("TEXTCOLOR",     (0,0), (-1,0),  WHITE),
            # Grid
            ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#9ca3af")),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("LEFTPADDING",   (0,0), (-1,-1), 3),
            ("RIGHTPADDING",  (0,0), (-1,-1), 3),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            # Alternating rows
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, colors.HexColor("#f0fff4")]),
            # Left green border on data rows
            ("LINEAFTER",     (0,0), (0,-1),  2, GREEN),
        ]))
        story.append(pred_t)
        story.append(Spacer(1, 8*mm))

        # ── 8. COUNSELLOR'S NOTE ──────────────────────────────
        story.append(Paragraph(
            "<b>Counsellor's Note</b>",
            ps("cn", fontSize=10.5, fontName="Helvetica-Bold",
               textColor=TEXT, spaceAfter=5)
        ))
        note_s = ps("ns", fontSize=8.5, fontName="Helvetica",
                    textColor=MUTED, leading=12, spaceAfter=4)
        notes = [
            "This list is a <b>prediction</b> based on your score/rank and is <b>not an official CAP allotment or admission list</b>.",
            "The predictions are prepared using <b>previous CAP cut-offs, your category, rank, institute trends, seat availability</b>, and other admission parameters.",
            "The <b>Probability (%)</b> indicates the likelihood of admission based on available data. It is meant to help you make informed decisions while filling your CAP option form and <b>does not guarantee admission</b>.",
            "The <b>fees shown are approximate annual tuition fees</b>. The actual payable fees may vary depending on your category, scholarship eligibility, admission quota, and the institute's latest fee structure.",
            "Cut-offs may change every year based on the number of applicants, seat availability, reservation policies, and students' option preferences.",
            "We recommend including a <b>balanced mix of Dream, Target, and Safe colleges</b> in your option form to maximize your chances of securing admission.",
            "Before confirming admission, please verify the latest fee structure, eligibility, and admission rules from the respective institute and the official CAP notifications.",
            "For the best admission outcome, consult your counsellor before finalizing your college preferences and option form.",
        ]
        for i, note in enumerate(notes, 1):
            story.append(Paragraph(f"{i}. {note}", note_s))

        # ── 9. FOOTER ─────────────────────────────────────────
        def draw_footer(canvas, doc):
            canvas.saveState()
            canvas.setFillColor(GREEN)
            canvas.rect(M, 6*mm, PAGE_W - 2*M, 8*mm, fill=1, stroke=0)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.setFillColor(WHITE)
            canvas.drawCentredString(
                PAGE_W / 2, 9*mm,
                "📍 Sunny Pride, JM Road, Z Bridge, Deccan Gymkhana, Pune, Maharashtra 411004"
            )
            canvas.restoreState()

        doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
        buf.seek(0)

        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        filename  = f"{safe_name or 'Student'}_Cut-off_Analysis.pdf"

        return send_file(buf, mimetype="application/pdf",
                         as_attachment=True, download_name=filename)

    except Exception as e:
        logger.exception("PDF generation failed")
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500