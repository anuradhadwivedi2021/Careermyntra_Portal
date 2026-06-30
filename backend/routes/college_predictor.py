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

# ─── Helper: chance label based on percentile vs cutoff ─────
def _chance_label(student_percentile, cutoff_percentile):
    if cutoff_percentile is None:
        return "Unknown"
    diff = float(student_percentile) - float(cutoff_percentile)
    if diff >= 5:
        return "Safe"
    elif diff >= 0:
        return "Moderate"
    else:
        return "Dream"

CHANCE_ORDER = {"Safe": 0, "Moderate": 1, "Dream": 2, "Unknown": 3}

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
        "category": "category_full",       # Full code (GOPENS, GSCS...) — keep for reference
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
                row.get("district"),
                row.get("university"),
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
                _map_gender(row.get("gender_code")),
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
    cur.execute("SELECT DISTINCT district FROM cap_cutoff_data WHERE district IS NOT NULL ORDER BY district")
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

    conn = get_connection()
    cur = get_cursor(conn)

    # Build dynamic query
    where_clauses = [
        "exam_type = %s",
        "category = %s",
        "cap_year = %s",
    ]
    params = [exam_type, category, cap_year]

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

    # Calculate chances and sort
    results = []
    for r in rows:
        chance = _chance_label(percentile, r["cutoff_percentile"])
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
            "admission_chance":   chance,
            "gender_label":       r["gender"] if r["gender"] else "All",
            "quota_code":         r["quota_code"] if r["quota_code"] else "S",
            "is_autonomous":      r["is_autonomous"] if r["is_autonomous"] else False,
            "course_name":        r["course_name"] if r["course_name"] else "",
        })

    # Compute applicable quota for each result based on student's home district
    home_univ = _get_home_university(home_district)
    for r in results:
        r["applicable_quota"] = _get_applicable_quota(
            r.get("university"), home_univ, r.get("is_autonomous", False)
        )
        r["home_university"] = home_univ or ""

    # Sort: Safe first, then Moderate, then Dream, Unknown last
    results.sort(key=lambda x: (CHANCE_ORDER.get(x["admission_chance"], 3),
                                 -(x["cutoff_percentile"] or 0)))

    safe     = [r for r in results if r["admission_chance"] == "Safe"]
    moderate = [r for r in results if r["admission_chance"] == "Moderate"]
    dream    = [r for r in results if r["admission_chance"] == "Dream"]
    unknown  = [r for r in results if r["admission_chance"] == "Unknown"]

    return jsonify({
        "total": len(results),
        "student_percentile": percentile,
        "summary": {
            "safe": len(safe),
            "moderate": len(moderate),
            "dream": len(dream),
        },
        "results": results
    })


# ─── DEBUG: See exact values stored in DB ───────────────────
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

# ─── 6. POST /college-predictor/download-pdf ─────────────────
@college_predictor_bp.route("/college-predictor/download-pdf", methods=["POST"])
def download_pdf():
    """
    Generates a College Prediction Report PDF matching the on-screen layout:
    header, student details summary, safe/moderate/dream counts, results table.
    Body: { student: {...}, results: [...] }
    """
    try:
        data = request.get_json(silent=True) or {}
        student = data.get("student", {}) or {}
        results = data.get("results", []) or []

        name        = (student.get("name") or "Student").strip()
        category    = student.get("category") or ""
        percentile  = student.get("percentile") or ""
        branches    = student.get("branches") or []
        districts   = student.get("districts") or []
        exam_type   = student.get("exam_type") or "MHT-CET"
        cap_year    = student.get("cap_year") or ""
        cap_round   = student.get("cap_round") or "All Rounds"
        gender      = student.get("gender") or ""

        safe_count     = sum(1 for r in results if r.get("admission_chance") == "Safe")
        moderate_count = sum(1 for r in results if r.get("admission_chance") == "Moderate")
        dream_count    = sum(1 for r in results if r.get("admission_chance") == "Dream")

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=landscape(A4),
            topMargin=14 * mm, bottomMargin=14 * mm,
            leftMargin=14 * mm, rightMargin=14 * mm,
        )
        styles = getSampleStyleSheet()
        story = []

        title_style = ParagraphStyle(
            "ReportTitle", parent=styles["Title"], fontSize=18,
            textColor=colors.HexColor("#0d1b3e"), alignment=TA_LEFT, spaceAfter=2,
        )
        sub_style = ParagraphStyle(
            "ReportSub", parent=styles["Normal"], fontSize=10,
            textColor=colors.HexColor("#6b7280"), spaceAfter=10,
        )
        label_style = ParagraphStyle(
            "Label", parent=styles["Normal"], fontSize=8,
            textColor=colors.HexColor("#9ca3af"), spaceAfter=0,
        )
        value_style = ParagraphStyle(
            "Value", parent=styles["Normal"], fontSize=10,
            textColor=colors.HexColor("#0d1b3e"), spaceAfter=6, leading=13,
        )

        # ── Title ──
        story.append(Paragraph(f"{name} — College Prediction Report", title_style))
        story.append(Paragraph(
            f"{exam_type} | Percentile: {percentile} | {cap_year}", sub_style
        ))

        # ── Student details grid ──
        def field(lbl, val):
            if not val:
                return None
            return [Paragraph(lbl.upper(), label_style), Paragraph(str(val), value_style)]

        details = []
        details.append(field("Student Name", name))
        details.append(field("Caste Category", category))
        details.append(field("Entrance Exam", exam_type))
        details.append(field("Course", student.get("course") or ""))
        details.append(field("Percentile", percentile))
        if gender:
            details.append(field("Gender", gender))
        details = [d for d in details if d]

        # Build a grid table for details, 5 columns
        if details:
            rows = []
            row = []
            for i, d in enumerate(details):
                cell = [d[0], d[1]]
                row.append(cell)
                if len(row) == 5:
                    rows.append(row); row = []
            if row:
                while len(row) < 5:
                    row.append([Paragraph("", label_style), Paragraph("", value_style)])
                rows.append(row)

            detail_table_data = []
            for row in rows:
                detail_table_data.append([c[0] for c in row])
                detail_table_data.append([c[1] for c in row])

            t = Table(detail_table_data, colWidths=[52 * mm] * 5)
            t.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(t)
            story.append(Spacer(1, 4))

        if branches:
            story.append(Paragraph("PREFERRED BRANCHES", label_style))
            story.append(Paragraph(", ".join(branches), value_style))
        if districts:
            story.append(Paragraph("PREFERRED DISTRICTS", label_style))
            story.append(Paragraph(", ".join(districts), value_style))
        story.append(Paragraph("CAP ROUND", label_style))
        story.append(Paragraph(cap_round, value_style))
        story.append(Spacer(1, 10))

        # ── Summary boxes (Safe / Moderate / Dream) ──
        summary_data = [[
            Paragraph(f"<b><font size=16 color='#16a34a'>{safe_count}</font></b><br/>"
                      f"<font size=8 color='#6b7280'>Safe</font>", styles["Normal"]),
            Paragraph(f"<b><font size=16 color='#d97706'>{moderate_count}</font></b><br/>"
                      f"<font size=8 color='#6b7280'>Moderate</font>", styles["Normal"]),
            Paragraph(f"<b><font size=16 color='#dc2626'>{dream_count}</font></b><br/>"
                      f"<font size=8 color='#6b7280'>Dream</font>", styles["Normal"]),
        ]]
        sbox = Table(summary_data, colWidths=[88 * mm, 88 * mm, 88 * mm])
        sbox.setStyle(TableStyle([
            ("BOX", (0, 0), (0, 0), 0.75, colors.HexColor("#bbf7d0")),
            ("BOX", (1, 0), (1, 0), 0.75, colors.HexColor("#fde68a")),
            ("BOX", (2, 0), (2, 0), 0.75, colors.HexColor("#fecaca")),
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f0fdf4")),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#fffbeb")),
            ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#fef2f2")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(sbox)
        story.append(Spacer(1, 12))

        # ── Results table ──
        header = ["College Name", "Branch", "University", "Status", "Quota",
                   "District", "Cut-off", "Fees (\u20b9)", "Probability"]
        table_data = [header]
        for r in results:
            chance = r.get("admission_chance", "Dream")
            quota  = " + ".join(r.get("applicable_quota") or ["State"])
            fees   = f"\u20b9{int(r['fees']):,}" if r.get("fees") else "-"
            cutoff = f"{float(r['cutoff_percentile']):.2f}" if r.get("cutoff_percentile") is not None else "-"
            prob   = "80-95%" if chance == "Safe" else "50-79%" if chance == "Moderate" else "<50%"
            table_data.append([
                Paragraph(str(r.get("college_name", "")), styles["Normal"]),
                Paragraph(str(r.get("branch_name", "")), styles["Normal"]),
                Paragraph(str(r.get("university") or "-"), styles["Normal"]),
                chance,
                quota,
                r.get("district") or "-",
                cutoff,
                fees,
                prob,
            ])

        col_widths = [50*mm, 42*mm, 38*mm, 20*mm, 32*mm, 22*mm, 18*mm, 20*mm, 22*mm]
        rt = Table(table_data, colWidths=col_widths, repeatRows=1)
        chance_colors = {"Safe": colors.HexColor("#16a34a"),
                          "Moderate": colors.HexColor("#d97706"),
                          "Dream": colors.HexColor("#dc2626")}
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565c0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7.5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8faff")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for i, r in enumerate(results, start=1):
            chance = r.get("admission_chance", "Dream")
            style_cmds.append(("TEXTCOLOR", (3, i), (3, i), chance_colors.get(chance, colors.black)))
            style_cmds.append(("FONTNAME", (3, i), (3, i), "Helvetica-Bold"))
        rt.setStyle(TableStyle(style_cmds))
        story.append(rt)

        doc.build(story)
        buf.seek(0)

        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        filename = f"{safe_name or 'Student'}_College_Prediction.pdf"

        return send_file(
            buf, mimetype="application/pdf",
            as_attachment=True, download_name=filename,
        )

    except Exception as e:
        logger.exception("PDF generation failed")
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500