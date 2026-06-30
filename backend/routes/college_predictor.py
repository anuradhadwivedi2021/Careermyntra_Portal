# routes/college_predictor.py — College Predictor Blueprint
# Handles: CAP cutoff CSV/Excel upload (admin), prediction API, district list

from flask import Blueprint, request, jsonify
import os
import io
import re
import pandas as pd
from db import get_connection, get_cursor
from logger_setup import get_logger

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

    # ── Normalize column names: strip, lowercase, spaces→underscore ──
    df = df.rename(columns={c: c.strip().lower().replace(" ", "_") for c in df.columns})

    # Fix common misspellings in source files
    if "admission_autherity" in df.columns:
        df = df.rename(columns={"admission_autherity": "admission_authority"})

    # If both 'category' and 'category(1)' exist, 'category' is the FULL code
    # (e.g. GOPENS) and 'category(1)' is the SIMPLE code (e.g. OPEN) we want.
    # Rename in the right order so the simple one wins.
    if "category(1)" in df.columns:
        if "category" in df.columns:
            df = df.rename(columns={"category": "category_full"})
        df = df.rename(columns={"category(1)": "category"})

    # ── Column alias mapping — handle different source file formats ──
    ALIASES = {
        "institute_code":      "college_code",
        "inst_code":           "college_code",
        "institute_name":      "college_name",
        "inst_name":           "college_name",
        "branch":              "branch_name",
        "branch_code":         "branch_code",
        "course":              "course_name",
        "gender":              "gender_code",     # G, L, P, D, T, O, E, M
        "quota":               "quota_code",      # S, H, N, I, O
        "percentile":          "cutoff_percentile",
        "rank":                "cutoff_score",
        "year":                "cap_year",
        "cap_round":           "cap_round",
        "round":               "cap_round",
        "status":              "status_full",     # Full status string
        "entrance_exam":       "exam_type",
        "admission_authority": "admission_authority",
        "sub_category":        "sub_category",
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
                    gender, quota_code, is_autonomous, course_name, admission_authority
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,%s
                )
                ON CONFLICT (college_name, branch_name, cap_year, cap_round, category, seat_type)
                DO UPDATE SET
                    college_code         = EXCLUDED.college_code,
                    district             = EXCLUDED.district,
                    university           = EXCLUDED.university,
                    exam_type            = EXCLUDED.exam_type,
                    cutoff_percentile    = EXCLUDED.cutoff_percentile,
                    cutoff_score         = EXCLUDED.cutoff_score,
                    fees                 = EXCLUDED.fees,
                    naac_grade           = EXCLUDED.naac_grade,
                    nba_accredited       = EXCLUDED.nba_accredited,
                    placement_highest    = EXCLUDED.placement_highest,
                    placement_average    = EXCLUDED.placement_average,
                    website              = EXCLUDED.website,
                    address              = EXCLUDED.address,
                    gender               = EXCLUDED.gender,
                    quota_code           = EXCLUDED.quota_code,
                    is_autonomous        = EXCLUDED.is_autonomous,
                    course_name          = EXCLUDED.course_name,
                    admission_authority  = EXCLUDED.admission_authority,
                    updated_at           = CURRENT_TIMESTAMP
            """, (
                row.get("college_code"),
                row.get("college_name"),
                row.get("branch_name"),
                row.get("district"),
                row.get("university"),
                row.get("cap_year"),
                row.get("cap_round"),
                row.get("category"),
                row.get("seat_type", "AI"),
                row.get("exam_type") or "MHT-CET",
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
                row.get("admission_authority"),
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
    """Returns distinct course_name values, optionally filtered by exam_type"""
    exam_type = request.args.get("exam_type", "")
    conn = get_connection()
    cur = get_cursor(conn)
    if exam_type:
        cur.execute("""
            SELECT DISTINCT course_name FROM cap_cutoff_data
            WHERE exam_type = %s AND course_name IS NOT NULL
            ORDER BY course_name
        """, (exam_type,))
    else:
        cur.execute("""
            SELECT DISTINCT course_name FROM cap_cutoff_data
            WHERE course_name IS NOT NULL
            ORDER BY course_name
        """)
    courses = [r["course_name"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(courses)


# ─── NEW: GET /college-predictor/universities — unique university list ──
@college_predictor_bp.route("/college-predictor/universities", methods=["GET"])
def get_universities():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT DISTINCT university FROM cap_cutoff_data
        WHERE university IS NOT NULL AND university != ''
        ORDER BY university
    """)
    rows = [r["university"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── GET /college-predictor/branches?district=Pune&course=B.Tech ────
@college_predictor_bp.route("/college-predictor/branches", methods=["GET"])
def get_branches():
    """Returns distinct branch_name values, optionally filtered by district and/or course"""
    district = request.args.get("district", "")
    course   = request.args.get("course", "")
    conn = get_connection()
    cur = get_cursor(conn)

    where  = ["branch_name IS NOT NULL"]
    params = []
    if district:
        where.append("district = %s")
        params.append(district)
    if course:
        where.append("course_name = %s")
        params.append(course)

    where_sql = " AND ".join(where)
    cur.execute(f"""
        SELECT DISTINCT branch_name FROM cap_cutoff_data
        WHERE {where_sql}
        ORDER BY branch_name
    """, params)
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
    cur.execute("SELECT DISTINCT gender FROM cap_cutoff_data WHERE gender IS NOT NULL ORDER BY gender")
    genders = [r["gender"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT seat_type FROM cap_cutoff_data WHERE seat_type IS NOT NULL ORDER BY seat_type")
    seat_types = [r["seat_type"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT course_name FROM cap_cutoff_data WHERE course_name IS NOT NULL ORDER BY course_name")
    course_names = [r["course_name"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT admission_authority FROM cap_cutoff_data WHERE admission_authority IS NOT NULL AND admission_authority != '' ORDER BY admission_authority")
    authorities = [r["admission_authority"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT university FROM cap_cutoff_data WHERE university IS NOT NULL AND university != '' ORDER BY university")
    universities = [r["university"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({
        "years": years,
        "rounds": rounds,
        "categories": categories,
        "exam_types": exam_types,
        "genders": genders,
        "seat_types": seat_types,
        "course_names": course_names,
        "admission_authorities": authorities,
        "universities": universities,
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
    universities = data.get("universities", [])
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
        where_clauses.append(f"TRIM(district) IN ({placeholders})")
        params.extend(districts)

    if universities:
        placeholders = ",".join(["%s"] * len(universities))
        where_clauses.append(f"university IN ({placeholders})")
        params.extend(universities)

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
            gender, quota_code, is_autonomous, course_name, admission_authority
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
            "admission_authority": r["admission_authority"] if r["admission_authority"] else "",
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

# ─── 6. POST /college-predictor/download-pdf ── Generate PDF ────────────────
@college_predictor_bp.route("/college-predictor/download-pdf", methods=["POST"])
def download_prediction_pdf():
    """
    POST body (JSON):
    {
      student: { name, category, percentile, branches, districts },
      results: [ { college_name, branch_name, district, university,
                   cap_round, cutoff_percentile, fees, admission_chance }, ... ]
    }
    Returns a PDF file download.
    """
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from flask import send_file

    data    = request.get_json(silent=True) or {}
    student = data.get("student", {})
    results = data.get("results", [])

    if not results:
        return jsonify({"error": "No results to generate PDF"}), 400

    student_name       = student.get("name", "Student").strip() or "Student"
    student_category   = student.get("category", "—")
    student_percentile = student.get("percentile", "—")
    student_branches   = ", ".join(student.get("branches", [])) or "All Branches"
    student_cities     = ", ".join(student.get("districts", [])) or "All Districts"

    safe_name = re.sub(r"[^A-Za-z0-9_\- ]", "", student_name).strip().replace(" ", "_")
    filename  = f"{safe_name}_College_Prediction.pdf"

    # ── Colours ──────────────────────────────────────────────
    CM_GREEN      = colors.HexColor("#1A7A3E")
    CM_DARK_GREEN = colors.HexColor("#145C2E")
    CM_LIGHT_BG   = colors.HexColor("#F0FAF4")
    CM_BORDER     = colors.HexColor("#B2DFCC")
    CM_TEXT       = colors.HexColor("#1A1A1A")
    CM_MUTED      = colors.HexColor("#6B6B6B")
    CM_SAFE       = colors.HexColor("#16A34A")
    CM_MODERATE   = colors.HexColor("#D97706")
    CM_DREAM      = colors.HexColor("#DC2626")

    def S(name, **kw): return ParagraphStyle(name, **kw)

    sty_info    = S("info",  fontSize=10, textColor=CM_TEXT,    fontName="Helvetica",           spaceAfter=3)
    sty_tbl_hd  = S("thd",  fontSize=9,  textColor=colors.white, fontName="Helvetica-Bold",    alignment=TA_CENTER)
    sty_cell    = S("cell",  fontSize=8,  textColor=CM_TEXT,    fontName="Helvetica",           leading=11)
    sty_cell_c  = S("cellc", fontSize=8, textColor=CM_TEXT,    fontName="Helvetica",           alignment=TA_CENTER, leading=11)
    sty_safe    = S("safe",  fontSize=8,  textColor=CM_SAFE,    fontName="Helvetica-Bold",      alignment=TA_CENTER)
    sty_mod     = S("mod",   fontSize=8,  textColor=CM_MODERATE, fontName="Helvetica-Bold",     alignment=TA_CENTER)
    sty_dream   = S("drm",   fontSize=8,  textColor=CM_DREAM,   fontName="Helvetica-Bold",      alignment=TA_CENTER)
    sty_note    = S("note",  fontSize=8,  textColor=CM_MUTED,   fontName="Helvetica-Oblique",   leading=12)
    sty_footer  = S("ft",    fontSize=7,  textColor=CM_MUTED,   fontName="Helvetica",           alignment=TA_CENTER)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=12*mm, bottomMargin=15*mm,
        title=f"{student_name} – College Prediction",
        author="CareerMyntra",
    )
    story = []

    # ── Header ───────────────────────────────────────────────
    hdr = Table([[
        Paragraph("<b>CAREER MYNTRA</b>",
                  S("lg", fontSize=20, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)),
        Paragraph("Aptitude Test | Mock Exams | Admission Guidance | Skills Dev. | Jobs",
                  S("tg", fontSize=9,  textColor=colors.white, fontName="Helvetica",       alignment=TA_CENTER)),
    ]], colWidths=[55*mm, 125*mm])
    hdr.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), CM_GREEN),
        ("VALIGN",       (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,0),(-1,-1), 10),
        ("BOTTOMPADDING",(0,0),(-1,-1), 10),
    ]))
    story.append(hdr)

    # Contact strip
    ct = Table([[
        Paragraph("+91 98609 38338",   S("c1", fontSize=8, textColor=CM_DARK_GREEN, fontName="Helvetica")),
        Paragraph("info@careermyntra.com", S("c2", fontSize=8, textColor=CM_DARK_GREEN, fontName="Helvetica", alignment=TA_CENTER)),
        Paragraph("https://careermyntra.com", S("c3", fontSize=8, textColor=CM_DARK_GREEN, fontName="Helvetica", alignment=TA_RIGHT)),
    ]], colWidths=[60*mm, 60*mm, 60*mm])
    ct.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), CM_LIGHT_BG),
        ("TOPPADDING",   (0,0),(-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING",  (0,0),(-1,-1), 6),
        ("RIGHTPADDING", (0,0),(-1,-1), 6),
        ("BOX",          (0,0),(-1,-1), 0.5, CM_BORDER),
    ]))
    story.append(ct)
    story.append(Spacer(1, 5*mm))

    # ── Student info ─────────────────────────────────────────
    story.append(Paragraph(f"Full Name: {student_name}",           sty_info))
    story.append(Paragraph(f"Caste Category: {student_category}",  sty_info))
    story.append(Paragraph(f"MHT-CET PCM Percentile: {student_percentile}", sty_info))
    if student.get("branches"):
        story.append(Paragraph(f"Preferred Branches: {student_branches}", sty_info))
    if student.get("districts"):
        story.append(Paragraph(f"Preferred City: {student_cities}", sty_info))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph("College Prediction List",
                            S("pt", fontSize=14, textColor=CM_TEXT, fontName="Helvetica-Bold",
                              alignment=TA_CENTER, spaceAfter=6)))

    # ── Prediction Table ─────────────────────────────────────
    col_w = [8*mm, 45*mm, 32*mm, 22*mm, 16*mm, 22*mm, 22*mm, 13*mm]
    heads = [Paragraph(h, sty_tbl_hd) for h in
             ["Sr.", "College Name", "Branches", "Status", "District", "Cut-off", "Fees (Rs.)", "Chance"]]
    rows  = [heads]

    CHANCE_LBL = {"Safe": "High", "Moderate": "Medium", "Dream": "Very Low", "Unknown": "—"}

    for i, r in enumerate(results, 1):
        chance    = r.get("admission_chance", "Unknown")
        cutoff    = r.get("cutoff_percentile")
        cutoff_s  = f"{float(cutoff):.2f} %ile" if cutoff is not None else "—"
        fees_raw  = r.get("fees")
        fees_s    = f"Rs.{int(fees_raw):,}" if fees_raw else "—"
        prob_s    = CHANCE_LBL.get(chance, "—")
        univ      = r.get("university") or r.get("seat_type") or "—"
        ch_sty    = sty_safe if chance == "Safe" else (sty_mod if chance == "Moderate" else sty_dream)

        rows.append([
            Paragraph(str(i),                     sty_cell_c),
            Paragraph(r.get("college_name", "—"), sty_cell),
            Paragraph(r.get("branch_name",  "—"), sty_cell),
            Paragraph(univ,                       sty_cell_c),
            Paragraph(r.get("district",     "—"), sty_cell_c),
            Paragraph(f"<b>{cutoff_s}</b>",
                      S("co", fontSize=8, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph(fees_s,                     sty_cell_c),
            Paragraph(prob_s,                     ch_sty),
        ])

    pred_t = Table(rows, colWidths=col_w, repeatRows=1)
    pred_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  CM_GREEN),
        ("TEXTCOLOR",     (0,0), (-1,0),  colors.white),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 3),
        ("RIGHTPADDING",  (0,0), (-1,-1), 3),
        ("GRID",          (0,0), (-1,-1), 0.4, CM_BORDER),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, CM_LIGHT_BG]),
    ]))
    story.append(pred_t)
    story.append(Spacer(1, 6*mm))

    # ── Counsellor Notes ─────────────────────────────────────
    story.append(Paragraph("Counsellor's Note",
                            S("cn", fontSize=10, fontName="Helvetica-Bold",
                              textColor=CM_TEXT, spaceBefore=4, spaceAfter=4)))
    notes = [
        "This list is a <b>prediction</b> based on your score/rank and is <b>not an official CAP allotment or admission list</b>.",
        "The predictions are prepared using <b>previous CAP cut-offs, your category, rank, institute trends, seat availability, and other admission parameters</b>.",
        "The <b>Probability</b> indicates the likelihood of admission based on available data and <b>does not guarantee admission</b>.",
        "Fees shown are approximate annual tuition fees and may vary based on category and scholarship eligibility.",
        "Cut-offs may change every year based on applicants, seat availability, and reservation policies.",
        "We recommend including a <b>balanced mix of Dream, Target, and Safe colleges</b> in your option form.",
        "Before confirming admission, verify the latest fee structure and eligibility from the institute and official CAP notifications.",
        "For the best outcome, consult your counsellor before finalizing your college preferences.",
    ]
    for idx, note in enumerate(notes, 1):
        story.append(Paragraph(f"{idx}. {note}", sty_note))
        story.append(Spacer(1, 1.5*mm))

    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=CM_GREEN))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "Sunny Pride, JM Road, Z Bridge, Deccan Gymkhana, Pune, Maharashtra 411004",
        sty_footer
    ))

    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf",
                     as_attachment=True, download_name=filename)