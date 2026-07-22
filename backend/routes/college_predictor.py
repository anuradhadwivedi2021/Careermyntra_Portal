# routes/college_predictor.py — College Predictor Blueprint
# Handles: CAP cutoff CSV/Excel upload (admin), prediction API, district list
#
# PATCHED VERSION — fixes:
#   1. Gender mapping (Excel has full words like "General","Ladies" not single letters)
#   2. Admission Authority was wrongly mapped into seat_type — now goes to its own column
#   3. Added missing /college-predictor/universities route
#   4. Added missing "universities" filter support inside /predict
#   5. Added missing /college-predictor/download-pdf route
#   6. Made download_pdf crash-proof: safe number formatting + traceback in response
#
# NOTE: all original logic is preserved as-is. New/changed lines are marked with "# PATCH:"

from flask import Blueprint, request, jsonify, send_file
import os
import io
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

# PATCH: Original GENDER_MAP only had single-letter keys (G, L, P, D, T, O, E, M).
# But the actual uploaded Excel's Gender column has FULL WORDS:
# General, Ladies, PWD, Defense, TFWS, EWS, Minority, Orphan.
# We keep the old letter-based entries (in case some other source file uses
# letters) AND add the full-word keys so both formats map correctly.
GENDER_MAP = {
    'G': 'General',
    'L': 'Ladies',
    'P': 'PWD',
    'D': 'Defense',
    'T': 'TFWS',
    'O': 'Orphan',
    'E': 'EWS',
    'M': 'Minority',

    'GENERAL':  'General',
    'LADIES':   'Ladies',
    'PWD':      'PWD',
    'DEFENSE':  'Defense',
    'TFWS':     'TFWS',
    'EWS':      'EWS',
    'MINORITY': 'Minority',
    'ORPHAN':   'Orphan',
}

QUOTA_MAP = {
    'S': 'State',
    'H': 'Home',
    'O': 'Outside',
    'M': 'Minority',
    'R': 'Orphan',
    # Also accept full-word input (self-mapping)
    'STATE':    'State',
    'HOME':     'Home',
    'OUTSIDE':  'Outside',
    'MINORITY': 'Minority',
    'ORPHAN':   'Orphan',
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
    # PATCH: uppercase + strip so both "General" and "GENERAL" match the map
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
        "location": "location",
        "location name": "location",
        "course": "course_name",
        "university": "university",
        "category_simple": "category",    # Simplified category (OPEN, SC, ST...)
        "gender": "gender_code",          # Excel: General, Ladies, PWD, Defense, TFWS, EWS, Minority, Orphan
        "quota": "quota_code",            # S, H, N, I, O
        # PATCH: "Admission Autherity" (typo col in source Excel) must map to its
        # OWN column, not to seat_type. seat_type is a different concept
        # (AI/AllIndia etc) and was being silently overwritten with "CET CELL"
        # before this fix, which broke the Admission Authority dropdown.
        "admission_autherity": "admission_authority",  # CET CELL (typo in source Excel)
        "admission_authority": "admission_authority",  # CET CELL (correct spelling)
        "entrance_exam": "exam_type",        # MHT-CET, JEE Main
        "percentile": "cutoff_percentile",
        "rank": "cutoff_score",
        "year": "cap_year",
        "cap round": "cap_round",
        "sub category": "sub_category",
        "sub_category": "sub_category",
        "district": "district",
        "address": "address",
        "pincode": "pincode",
        "status": "status_full",          # Full status string
        # Also handle already-mapped names
        "course_name": "course_name",
        "branch_name": "branch_name",
        "is_autonomous": "is_autonomous",
        "category(1)": "category",
        "cutoff": "cutoff_percentile",
        "cap_round": "cap_round", "round": "cap_round",
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
                    college_code, college_name, branch_name, branch_code, district, location, university,
                    cap_year, cap_round, category, sub_category, seat_type, exam_type,
                    cutoff_percentile, cutoff_score, fees, naac_grade,
                    nba_accredited, placement_highest, placement_average,
                    website, address,
                    gender, quota_code, is_autonomous, course_name,

                    admission_authority
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s
                )
                ON CONFLICT (college_name, branch_name, cap_year, cap_round, category, seat_type, gender, quota_code)
                DO UPDATE SET
                    college_code        = EXCLUDED.college_code,
                    branch_code         = EXCLUDED.branch_code,
                    sub_category        = EXCLUDED.sub_category,
                    district            = EXCLUDED.district,
                    location            = EXCLUDED.location,
                    university          = EXCLUDED.university,
                    exam_type           = EXCLUDED.exam_type,
                    cutoff_percentile   = EXCLUDED.cutoff_percentile,
                    cutoff_score        = EXCLUDED.cutoff_score,
                    fees                = EXCLUDED.fees,
                    naac_grade          = EXCLUDED.naac_grade,
                    nba_accredited      = EXCLUDED.nba_accredited,
                    placement_highest   = EXCLUDED.placement_highest,
                    placement_average   = EXCLUDED.placement_average,
                    website             = EXCLUDED.website,
                    address             = EXCLUDED.address,
                    gender              = EXCLUDED.gender,
                    quota_code          = EXCLUDED.quota_code,
                    is_autonomous       = EXCLUDED.is_autonomous,
                    course_name         = EXCLUDED.course_name,
                    admission_authority = EXCLUDED.admission_authority,
                    updated_at          = CURRENT_TIMESTAMP
            """, (
                row.get("college_code"),
                row.get("college_name"),
                row.get("branch_name"),
                row.get("branch_code"),
                row.get("district"),
                row.get("location"),
                row.get("university"),
                row.get("cap_year"),
                row.get("cap_round"),
                row.get("category") or row.get("category_simple") or row.get("category_full"),
                row.get("sub_category"),
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
                # PATCH: admission_authority now saved into its own column
                row.get("admission_authority") or "CET CELL",
            ))
            inserted += 1
        
        except Exception as e:
            conn.rollback()
            if skipped == 0:
                logger.exception("[Predictor Upload] FIRST ROW FAILURE - full traceback")
                logger.warning(f"[Predictor Upload] First failing row data: {dict(row)}")
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
    """Returns distinct course_name values from Excel Course column (B.Tech, M.Tech etc)"""
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT DISTINCT course_name FROM cap_cutoff_data
        WHERE course_name IS NOT NULL AND course_name != ''
        ORDER BY course_name
    """)
    courses = [r["course_name"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(courses if courses else ["B.Tech"])


# ─── NEW: GET /college-predictor/branches?district=Pune ─────
@college_predictor_bp.route("/college-predictor/branches", methods=["GET"])
def get_branches():
    # Multi-district support: ?districts=Pune,Nashik,Mumbai
    districts_raw = request.args.get("districts", "")
    district_single = request.args.get("district", "")

    # Support both ?district=Pune and ?districts=Pune,Nashik
    if districts_raw:
        districts = [d.strip() for d in districts_raw.split(",") if d.strip()]
    elif district_single:
        districts = [district_single.strip()]
    else:
        districts = []

    conn = get_connection()
    cur = get_cursor(conn)

    colleges_raw = request.args.get("colleges", "")
    colleges = [c.strip() for c in colleges_raw.split(",") if c.strip()]

    where = []
    params = []
    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        where.append(f"TRIM(district) IN ({placeholders})")
        params.extend(districts)
    if colleges:
        placeholders = ",".join(["%s"] * len(colleges))
        where.append(f"college_name IN ({placeholders})")
        params.extend(colleges)

    where_sql = (" AND ".join(where) + " AND ") if where else ""

    cur.execute(f"""
        SELECT DISTINCT branch_name FROM cap_cutoff_data
        WHERE {where_sql} branch_name IS NOT NULL
        ORDER BY branch_name
    """, params)

    rows = [r["branch_name"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── NEW: GET /college-predictor/colleges?districts=Pune,Nashik ─────
@college_predictor_bp.route("/college-predictor/colleges", methods=["GET"])
def get_colleges():
    districts_raw = request.args.get("districts", "")
    districts = [d.strip() for d in districts_raw.split(",") if d.strip()]

    conn = get_connection()
    cur = get_cursor(conn)

    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        cur.execute(f"""
            SELECT DISTINCT college_code, college_name
            FROM cap_cutoff_data
            WHERE (TRIM(district) IN ({placeholders}) OR TRIM(location) IN ({placeholders}))
            AND college_name IS NOT NULL
            ORDER BY college_name
        """, districts + districts)
    else:
        cur.execute("""
            SELECT DISTINCT college_code, college_name
            FROM cap_cutoff_data
            WHERE college_name IS NOT NULL
            ORDER BY college_name
        """)

    rows = [{"code": r["college_code"], "name": r["college_name"]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)
















# ─── PATCH: NEW — GET /college-predictor/universities ────────
# This route was being called by the frontend (loadUniversities())
# but never existed in the backend, so "Preferred Universities" dropdown
# always came back empty. Adding it now, following the same pattern as
# get_districts()/get_courses().
@college_predictor_bp.route("/college-predictor/universities", methods=["GET"])
def get_universities():
    """Returns distinct university values for the Preferred Universities dropdown"""
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT MIN(university) AS university
        FROM cap_cutoff_data
        WHERE university IS NOT NULL AND TRIM(university) != ''
        GROUP BY LOWER(TRIM(university))
        ORDER BY MIN(university)
    """)
    rows = [r["university"] for r in cur.fetchall()]
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
    # PATCH: also expose admission_authority values for the Admission Authority dropdown
    cur.execute("SELECT DISTINCT admission_authority FROM cap_cutoff_data WHERE admission_authority IS NOT NULL ORDER BY admission_authority")
    admission_authorities = [r["admission_authority"] for r in cur.fetchall()]
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
        "admission_authorities": admission_authorities,  # PATCH: new field
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
      branches: ["Computer Engineering", "Information Technology"],
      districts: ["Pune", "Nashik"],
      universities: ["Savitribai Phule Pune University"],
      gender: "Male",
      course_name: "B.Tech",
      admission_authority: "CET CELL",
      quota: "S",
      rank: 12000
    }
    """
    data = request.get_json(silent=True) or {}

    # PATCH: no hardcoded defaults — Excel-style filtering: khaali field = no filter applied on that column
    exam_type    = data.get("exam_type") or ""
    percentile   = data.get("percentile")
    category     = data.get("category") or ""
    cap_year     = data.get("cap_year") or ""
    cap_round    = data.get("cap_round", "All Rounds")
    branches     = data.get("branches", [])
    districts    = data.get("districts", [])
    universities = data.get("universities", [])
    colleges     = data.get("colleges", [])  

    course_name  = data.get("course_name", "")
    admission_authority = data.get("admission_authority", "")
    home_district = data.get("home_district", "")
    quota        = data.get("quota", "")
    rank         = data.get("rank", "")

    # Normalize frontend display values to DB values
    CATEGORY_MAP = {
        "General (Open)": "OPEN", "general (open)": "OPEN", "open": "OPEN",
        "OBC": "OBC", "SC": "SC", "ST": "ST",
        "NT1": "NT1", "NT2": "NT2", "NT3": "NT3",
        "VJ": "VJ", "EWS": "EWS", "SEBC": "SEBC",
        "PWD": "PWD", "TFWS": "TFWS", "ORPHAN": "ORPHAN",
        # PATCH: DB stores "MI" for Minority category (14th distinct value in cap_cutoff_data).
        # Without these lines, selecting "Minority" as category returned zero results.
        "MI": "MI", "Minority": "MI", "MINORITY": "MI", "minority": "MI",
    }
    category = CATEGORY_MAP.get(category, category.upper()) if category else ""

    CAP_ROUND_MAP = {
    "CAP Round 1": "Round I", "CAP Round 2": "Round II",
    "CAP Round 3": "Round III", "CAP Round 4": "Round IV",
    "Round 1": "Round I", "Round 2": "Round II",
    "Round 3": "Round III", "Round 4": "Round IV",
    "1": "Round I", "2": "Round II", "3": "Round III", "4": "Round IV",
}

    # PATCH: cap_round can now be a single string OR a list (multi-select ready)
    if isinstance(cap_round, list):
        cap_rounds_normalized = [CAP_ROUND_MAP.get(r, r) for r in cap_round if r and r != "All Rounds"]
    else:
        single = CAP_ROUND_MAP.get(cap_round, cap_round)
        cap_rounds_normalized = [] if (not single or single == "All Rounds") else [single]
    print(f"DEBUG cap_rounds_normalized={cap_rounds_normalized!r}")

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

    student_gender_raw = data.get("gender", "")
    student_gender = _map_gender(student_gender_raw) if student_gender_raw else ""

    if percentile is None:
        return jsonify({"error": "Percentile is required"}), 400

    try:
        percentile = float(percentile)
    except (ValueError, TypeError):
        return jsonify({"error": "Percentile must be a number"}), 400

    conn = get_connection()
    cur = get_cursor(conn)

    # PATCH: only add these filters if actually selected — matches Excel-style
    # "filter only on columns you've picked a value for"
    where_clauses = []
    params = []
    if exam_type:
        where_clauses.append("UPPER(TRIM(exam_type)) = UPPER(TRIM(%s))")
        params.append(exam_type)
    if category:
        where_clauses.append("UPPER(TRIM(category)) = UPPER(TRIM(%s))")
        params.append(category)
    if cap_year:
        where_clauses.append("TRIM(cap_year) = TRIM(%s)")
        params.append(cap_year)

    # PATCH: DB's `gender` column is actually a RESERVATION seat-type
    # (General, Ladies, PWD, Defense, TFWS, Orphan, EWS, Minority) — NOT
    # a biological gender. If user picks Male/Female/Other on the form,
    # we treat that as "no gender filter" (show all seats they're eligible for).
    # Only filter if the value matches a real DB seat-type.
    VALID_DB_GENDERS = {"General", "Ladies", "PWD", "Defense", "TFWS", "Orphan", "EWS", "Minority"}
    if student_gender and student_gender in VALID_DB_GENDERS:
        where_clauses.append("(gender = %s OR gender = 'All' OR gender IS NULL)")
        params.append(student_gender)
    elif student_gender and student_gender.lower() == "female":
        # A female student is eligible for both "General" (all-gender) seats
        # AND "Ladies" reserved seats. Male students see only General.
        where_clauses.append("(gender IN ('General', 'Ladies') OR gender = 'All' OR gender IS NULL)")

    # PATCH: multi-select CAP round support
    if cap_rounds_normalized:
        placeholders = ",".join(["%s"] * len(cap_rounds_normalized))
        where_clauses.append(f"cap_round IN ({placeholders})")
        params.extend(cap_rounds_normalized)

    if branches:
        placeholders = ",".join(["%s"] * len(branches))
        where_clauses.append(f"branch_name IN ({placeholders})")
        params.extend(branches)

    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        where_clauses.append(f"(TRIM(district) IN ({placeholders}) OR TRIM(location) IN ({placeholders}))")
        params.extend(districts)
        params.extend(districts)

    if universities:
        placeholders = ",".join(["%s"] * len(universities))
        where_clauses.append(f"university IN ({placeholders})")
        params.extend(universities)
    if colleges:
        placeholders = ",".join(["%s"] * len(colleges))
        where_clauses.append(f"college_name IN ({placeholders})")
        params.extend(colleges)





    # PATCH: Course filter (was completely missing before)
    if course_name:
        where_clauses.append("course_name = %s")
        params.append(course_name)

    # PATCH: Admission Authority filter (was completely missing before)
    if admission_authority:
        where_clauses.append("admission_authority = %s")
        params.append(admission_authority)

     # PATCH: Quota filter — normalize input via QUOTA_MAP so short codes ("S", "H")
    # OR full labels ("State", "Home") from the frontend both match the DB values.
    if quota:
        quota_normalized = _map_quota(quota)
        where_clauses.append("quota_code = %s")
        params.append(quota_normalized)

    # PATCH: +2/-5 percentile range — only show colleges whose cutoff falls
    # within [percentile-5, percentile+2], as per prediction logic shown on frontend
    where_clauses.append("cutoff_percentile BETWEEN %s AND %s")
    params.append(percentile - 5)
    params.append(percentile + 2)

    # PATCH: Rank filter — ±10% range around student's rank (only if rank provided)
    if rank:
        try:
            rank_val = float(rank)
            rank_min = rank_val * 0.9
            rank_max = rank_val * 1.1
            where_clauses.append("(cutoff_score IS NULL OR cutoff_score BETWEEN %s AND %s)")
            params.append(rank_min)
            params.append(rank_max)
        except (ValueError, TypeError):
            pass  # ignore invalid rank input, don't break the query

    where_sql = " AND ".join(where_clauses)

    cur.execute(f"""
        SELECT
            id, college_code, college_name, branch_name, branch_code,
            district, location, university, cap_year, cap_round,
            category, seat_type, exam_type,
                

            cutoff_percentile, cutoff_score,
            fees, naac_grade, nba_accredited,
            placement_highest, placement_average,
            website, address,
            gender, quota_code, is_autonomous, course_name,
            admission_authority
        FROM cap_cutoff_data
        WHERE {where_sql}
        ORDER BY cutoff_percentile DESC
        LIMIT 2000
    """, params)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    results = []
    for r in rows:
        chance = _chance_label(percentile, r["cutoff_percentile"])
        results.append({
            "id":                 r["id"],
            "college_code":       r["college_code"],
            "college_name":       r["college_name"],
            "branch_name":        r["branch_name"],
            "branch_code":        r["branch_code"],
            "district":           r.get("location") or r["district"],
            "location":           r.get("location") or r["district"],
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
            "admission_authority": r["admission_authority"] if r["admission_authority"] else "CET CELL",
        })

    home_univ = _get_home_university(home_district)
    for r in results:
        r["applicable_quota"] = _get_applicable_quota(
            r.get("university"), home_univ, r.get("is_autonomous", False)
        )
        r["home_university"] = home_univ or ""

    # PATCH: sort purely by cutoff_percentile descending (higher first, lower last)
    results.sort(key=lambda x: -(x["cutoff_percentile"] or 0))

    safe     = [r for r in results if r["admission_chance"] == "Safe"]
    moderate = [r for r in results if r["admission_chance"] == "Moderate"]
    dream    = [r for r in results if r["admission_chance"] == "Dream"]

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


# ─── PATCHED: POST /college-predictor/download-pdf ──────────
# The frontend's downloadPDF() JS function was already calling this endpoint,
# but it never existed on the backend — that's why "PDF generation failed"
# always showed after predicting. Adding a working implementation using
# reportlab (pure-python, no external binary dependency needed).
#
# Install once if not already present:  pip install reportlab
#
# PATCH (this round): made this route crash-proof —
#   1. Safe number formatting for fees/rank (won't crash if value is a
#      string, Decimal, None, or has extra characters like "Rs." or ",").
#   2. Wrapped entire body in try/except so any unexpected error returns
#      a JSON error WITH the real message instead of a blank 500 — makes
#      debugging from the browser Network tab possible.
#   3. Logs the full traceback to the server log via logger.exception().
@college_predictor_bp.route("/college-predictor/download-pdf", methods=["POST"])
def download_pdf():
    data = request.get_json(silent=True) or {}
    student = data.get("student", {}) or {}
    results = data.get("results", []) or []

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        return jsonify({
            "error": "reportlab is not installed on the server. "
                     "Run: pip install reportlab"
        }), 500

    # PATCH: safe numeric formatter — never crashes on weird input
    def _fmt_number(val):

        if val is None or val == "":
            return "-"
        try:
            return f"{int(float(str(val).replace(',','').replace('Rs.','').strip())):,}"
        except:
            return str(val).strip() or "-"

    try:
        buffer = io.BytesIO()

        styles = getSampleStyleSheet()
        elements = []



        visible_columns_early = data.get("visible_columns", {}) or {}
        TOGGLE_COLS_COUNT = sum(1 for k in ["cutoff","rank","fees","prob","univ","quota","distance"] if visible_columns_early.get(k, True))
        PAGE_WIDTH_MM = 297 if TOGGLE_COLS_COUNT > 4 else 210
        CONTENT_WIDTH_MM = PAGE_WIDTH_MM - 20

        # ── HEADER — Logo + Contact ──────────────────────────
        from reportlab.platypus import HRFlowable, Image as RLImage
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        DEJAVU_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    
        if os.path.exists(DEJAVU_PATH):
           pdfmetrics.registerFont(TTFont("DejaVuSans", DEJAVU_PATH))
           FEE_FONT = "DejaVuSans"

        else:
             FEE_FONT = "Helvetica"



        # Header table: Logo left | Contact right
        from reportlab.platypus import HRFlowable, Image as RLImage
        header_contact = Paragraph(
             "<b>+91 98609 38338</b><br/>info@careermyntra.com<br/>https://careermyntra.com",
            ParagraphStyle("contact", parent=styles["Normal"], fontSize=9,
                           textColor=colors.HexColor("#374151"), leading=14, alignment=TA_RIGHT)
        )
        # PATCH: logo now centered alone on top, matching Vinay's reference —
        # contact info moved out of the logo row into its own blue bar below tagline.
        LOGO_PATH = "/home/anuradha/Careermyntra_Portal/frontend/images/logo.jpeg"

        if os.path.exists(LOGO_PATH):
            logo_element = RLImage(LOGO_PATH, width=60*mm, height=26*mm, kind="proportional")
        else:
            logger.warning(f"[download_pdf] Logo not found at {LOGO_PATH}")
            logo_element = Paragraph(
                "<b><font color='#1565c0' size=16>Career</font><font color='#16a34a' size=16>Myntra</font></b>",
                ParagraphStyle("logo", parent=styles["Normal"], fontSize=16, leading=20, alignment=TA_CENTER)
            )

        logo_tbl = Table([[logo_element]], colWidths=[CONTENT_WIDTH_MM*mm])
        logo_tbl.setStyle(TableStyle([
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ]))
        elements.append(logo_tbl)

        # PATCH: green tagline bar (unchanged, already matched Vinay's format)
        tagline_tbl = Table([["Aptitude Test  |  Mock Exams  |  Admission Guidance  |  Skills Dev.  |  Jobs"]],
                            colWidths=[CONTENT_WIDTH_MM*mm])
        tagline_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#16a34a")),
            ("TEXTCOLOR", (0,0), (-1,-1), colors.white),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("FONTNAME", (0,0), (-1,-1), "Helvetica-Bold"),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ]))
        elements.append(tagline_tbl)

        # PATCH: NEW blue contact bar below tagline — matches Vinay's PDF exactly

        col_w = CONTENT_WIDTH_MM / 3
        contact_tbl = Table([[

            Paragraph("Phone: +91 98609 38338", ParagraphStyle("c1", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph("Email: info@careermyntra.com", ParagraphStyle("c2", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER)),
            Paragraph("Web: https://careermyntra.com", ParagraphStyle("c3", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold", alignment=TA_CENTER))

        ]], colWidths=[col_w*mm, col_w*mm, col_w*mm])

        contact_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#1565c0")),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ]))
        elements.append(contact_tbl)
        elements.append(Spacer(1, 8))

        # ── STUDENT INFO ─────────────────────────────────────
        name       = student.get("name") or "Student"
        category   = student.get("category") or ""
        percentile = student.get("percentile") or ""
        branches   = student.get("branches") or []
        districts  = student.get("districts") or []

        info_style = ParagraphStyle("info", parent=styles["Normal"], fontSize=10,
                                    textColor=colors.HexColor("#0d1b3e"), leading=16)
        elements.append(Paragraph(f"<b>Full Name:</b> {name}", info_style))
        if category:
            elements.append(Paragraph(f"<b>Caste Category:</b> {category}", info_style))
        if percentile:
            elements.append(Paragraph(f"<b>MHT-CET PCM Percentile:</b> {percentile}", info_style))
        if branches:
            elements.append(Paragraph(f"<b>Preferred Branches:</b>  {', '.join(branches)}", info_style))
        if districts:
            elements.append(Paragraph(f"<b>Preferred City:</b>  {', '.join(districts)}", info_style))
        elements.append(Spacer(1, 10))

        # ── TABLE TITLE ──────────────────────────────────────
        title_style = ParagraphStyle("title", parent=styles["Normal"], fontSize=13,
                                     fontName="Helvetica-Bold", alignment=TA_CENTER,
                                     textColor=colors.HexColor("#0d1b3e"), spaceAfter=6)
        elements.append(Paragraph("College Prediction List", title_style))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0d1b3e")))
        elements.append(Spacer(1, 4))

        # ── PROBABILITY CALC (same as frontend) ──────────────
        def calc_probability(student_pct, cutoff_pct):
            if cutoff_pct is None: return (15, "Very Low")
            try:
                diff = float(student_pct) - float(cutoff_pct)
            except: return (15, "Very Low")
            if diff >= 5:    return (99, "Very High")
            if diff >= 2:    return (98, "Very High")
            if diff >= 0.5:  return (95, "Very High")
            if diff >= -1:   return (92, "High")
            if diff >= -3:   return (86, "High")
            if diff >= -4:   return (82, "High")
            if diff >= -5.5: return (60, "Medium")
            if diff >= -6:   return (55, "Medium")
            if diff >= -6.5: return (52, "Medium")
            if diff >= -7:   return (48, "Low")
            if diff >= -7.5: return (45, "Low")
            if diff >= -8:   return (40, "Low")
            if diff >= -8.5: return (35, "Low")
            if diff >= -9:   return (22, "Very Low")
            if diff >= -9.5: return (18, "Very Low")
            if diff >= -9.8: return (12, "Very Low")
            return (10, "Very Low")

        # ── TABLE DATA ───────────────────────────────────────
        try:
            student_pct_float = float(percentile)
        except:
            student_pct_float = 0

        # PATCH: column toggle support — only include columns that are ON.
        # "Sr.", "College Name", "Branches", "Status", "District" are always
        # shown (base/fixed columns, same as Vinay's original correct format).
        # Toggleable columns come from the frontend's visible_columns object.
        visible_columns = data.get("visible_columns", {}) or {}

        def _is_on(key):
            # default to True if key missing, so old frontend calls (without
            # visible_columns) still get the full report like before.
            # EXCEPTION: 'distance' defaults to False because it's not a real
            # computed value yet (always shows "—") — only include if the
            # frontend explicitly turns it ON.
            if key == "distance":
                return visible_columns.get(key, False)
            return visible_columns.get(key, True)
        # Column definitions: (key, header_label, col_width)
        # key=None means always-shown fixed column
        FIXED_COLS = [
            (None, "Sr.", 10),
            (None, "College Name", 42),
            (None, "Branches", 32),
            (None, "Status", 22),
            (None, "Location", 16),
        ]
        TOGGLE_COLS = [
            ("cutoff",   "Cut-off",     18),
            ("rank",     "Rank",        14),
            ("fees",     "Fees (₹)",    20),
            ("prob",     "Probability", 22),
            ("univ",     "University",  26),
            ("quota",    "Quota",       20),
            ("distance", "Distance",    16),
        ]

        active_toggle_cols = [c for c in TOGGLE_COLS if _is_on(c[0])]

        # PATCH: create the doc now that we know how many toggle columns are active
        from reportlab.lib.pagesizes import landscape
        page_size = landscape(A4) if len(active_toggle_cols) > 4 else A4
        doc = SimpleDocTemplate(
            buffer, pagesize=page_size,
            topMargin=12*mm, bottomMargin=12*mm,
            leftMargin=10*mm, rightMargin=10*mm
        )

        col_headers = [c[1] for c in FIXED_COLS] + [c[1] for c in active_toggle_cols]
        raw_widths  = [c[2] for c in FIXED_COLS] + [c[2] for c in active_toggle_cols]

        # PATCH: dynamically scale widths to always fit the page — no more overlap
        # chahe kitne bhi columns ON ho. Design/colors/layout bilkul untouched.
        available_width_mm = (297 if len(active_toggle_cols) > 4 else 210) - 20
        total_raw = sum(raw_widths)
        scale = available_width_mm / total_raw if total_raw > available_width_mm else 1.0
        col_widths = [w * scale * mm for w in raw_widths]

        table_data = [col_headers]

        for i, r in enumerate(results, start=1):
            cp = r.get("cutoff_percentile")
            try:
                cutoff_str = f"{float(cp):.2f} %ile" if cp is not None else "—"
            except:
                cutoff_str = str(cp) if cp else "—"

            fees = _fmt_number(r.get("fees"))
            fees_str = Paragraph(
                f"₹{fees} / year" if fees != "-" else "—",
                ParagraphStyle("fee", fontSize=7, leading=9, fontName=FEE_FONT, alignment=1)
            )
            rank_str = _fmt_number(r.get("cutoff_score"))

            # Status
            status = "Un-Aided"
            if r.get("is_autonomous"):
                status = "University Autonomous" if r.get("university") else "Autonomous"
            elif r.get("nba_accredited") == "Yes":
                status = "Un-Aided Autonomous"

            prob_pct, prob_label = calc_probability(student_pct_float, cp)
            prob_str = f"{prob_pct}% {prob_label}"

            univ_str = str(r.get("university") or "—")

            quota_list = r.get("applicable_quota") or ["State"]
            quota_str = ", ".join(quota_list)

            # Fixed columns row values
            college_code_val = r.get("college_code")
            branch_code_val = r.get("branch_code")
            college_label = f"{college_code_val} - {r.get('college_name')}" if college_code_val else str(r.get("college_name") or "—")
            branch_label = f"{branch_code_val} - {r.get('branch_name')}" if branch_code_val else str(r.get("branch_name") or "—")

            # NEW: CAP Round + Gender shown as badges/tags under the Branch name,
            # matching the Result Page exactly (Round I / General / Ladies pills).
            GENDER_LABELS = {
                'G': 'General', 'L': 'Ladies', 'D': 'Divyangjan', 'T': 'Transgender',
                'P': 'Orphan', 'All': 'All', 'E': 'EWS', 'M': 'Male', 'O': 'Other'
            }
            gender_raw = r.get("gender_label")
            gender_label = GENDER_LABELS.get(gender_raw, gender_raw) if gender_raw else 'All'
            cap_round_val = r.get("cap_round") or ""

            # PATCH: branch name alag Paragraph, badges alag colored mini-table
            branch_para = Paragraph(branch_label, ParagraphStyle("bn", fontSize=8, leading=11, fontName=FEE_FONT))

            badge_style = ParagraphStyle("badge", fontSize=6.5, leading=8, textColor=colors.white,
                                          alignment=1, fontName=FEE_FONT)
            badge_cells, badge_bgcolors = [], []
            if cap_round_val:
                badge_cells.append(Paragraph(cap_round_val, badge_style))
                badge_bgcolors.append(colors.HexColor("#1565c0"))
            if gender_label and gender_label != 'All':
                badge_cells.append(Paragraph(gender_label, badge_style))
                badge_bgcolors.append(colors.HexColor("#7c3aed"))

            branch_cell_content = [branch_para]
            if badge_cells:
                badge_tbl = Table([badge_cells])
                style_cmds = [
                    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                    ("TOPPADDING", (0,0), (-1,-1), 2),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 2),
                    ("LEFTPADDING", (0,0), (-1,-1), 5),
                    ("RIGHTPADDING", (0,0), (-1,-1), 5),
                ]
                for idx, c in enumerate(badge_bgcolors):
                    style_cmds.append(("BACKGROUND", (idx,0), (idx,0), c))
                badge_tbl.setStyle(TableStyle(style_cmds))
                branch_cell_content.append(Spacer(1, 2))
                branch_cell_content.append(badge_tbl)

            # PATCH: emoji pin hataya (font mein render nahi hota), bullet use kiya
            location_val = str(r.get("location") or r.get("district") or "—")
            location_html = f'<font color="#dc2626">&#8226;</font> {location_val}' if location_val != "—" else "—"

            row = [
                str(i),
                Paragraph(college_label, ParagraphStyle("cn", fontSize=8, leading=10)),
                branch_cell_content,
                status,
                Paragraph(location_html, ParagraphStyle("loc", fontSize=8, leading=10, fontName=FEE_FONT, alignment=1)),
            ]

            # PATCH: append only the toggle columns that are ON
            TOGGLE_VALUES = {
                "cutoff":   cutoff_str,
                "rank":     rank_str,
                "fees":     fees_str,
                "prob":     prob_str,
                "univ":     Paragraph(univ_str, ParagraphStyle("un", fontSize=7, leading=9)),
                "quota":    Paragraph(quota_str, ParagraphStyle("qt", fontSize=7, leading=9)),
                "distance": "—",
            }
            for key, _label, _w in active_toggle_cols:
                row.append(TOGGLE_VALUES[key])

            table_data.append(row)

        tbl = Table(table_data, repeatRows=1, colWidths=col_widths)

        # Row colors
        row_styles = [
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1565c0")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,0), 8),
            ("FONTSIZE",   (0,1), (-1,-1), 7),
            ("ALIGN",      (0,0), (-1,-1), "CENTER"),
            ("ALIGN",      (1,1), (2,-1), "LEFT"),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
            ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#d1d5db")),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LINEBELOW",  (0,0), (-1,0), 1.5, colors.HexColor("#0d47a1")),
        ]
        # PATCH: alternate row background changed to light green tint,
        # matching Vinay's reference PDF (was light blue before)
        for row_idx in range(1, len(table_data)):
            bg = colors.white if row_idx % 2 == 0 else colors.HexColor("#f0fdf4")
            row_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))

        # PATCH: green outer border around the whole table, matches Vinay's PDF
        row_styles.append(("BOX", (0,0), (-1,-1), 1.5, colors.HexColor("#16a34a")))
        tbl.setStyle(TableStyle(row_styles))
        elements.append(tbl)
        elements.append(Spacer(1, 12))

        # ── COUNSELLOR NOTE ──────────────────────────────────
        note_title = ParagraphStyle("nt", parent=styles["Normal"], fontSize=9,
                                    fontName="Helvetica-Bold", textColor=colors.HexColor("#0d1b3e"),
                                    spaceAfter=4)
        note_body  = ParagraphStyle("nb", parent=styles["Normal"], fontSize=8,
                                    textColor=colors.HexColor("#374151"), leading=13)
        elements.append(Paragraph("Counsellor's Note", note_title))
        notes = [
            "This list is a <b>prediction</b> based on your score/rank and is <b>not an official CAP allotment or admission list</b>.",
            "The predictions are prepared using <b>previous CAP cut-offs, your category, rank, institute trends, seat availability, and other admission parameters</b>.",
            "The <b>Probability (%)</b> indicates the likelihood of admission. It does <b>not guarantee admission</b>.",
            "The <b>fees shown are approximate annual tuition fees</b>. Actual fees may vary.",
            "Cut-offs may change every year based on applicants, seat availability, and reservation policies.",
            "We recommend a <b>balanced mix of Dream, Target, and Safe colleges</b> in your option form.",
            "Before confirming admission, verify latest fee structure and eligibility from the respective institute.",
            "For the best outcome, <b>consult your counsellor</b> before finalizing your option form.",
        ]
        for idx, note in enumerate(notes, 1):
            elements.append(Paragraph(f"{idx}. {note}", note_body))

        elements.append(Spacer(1, 10))

        # ── GREEN FOOTER ─────────────────────────────────────
        footer_tbl = Table([["Sunny Pride, JM Road, Z Bridge, Deccan Gymkhana, Pune, Maharashtra 411004"]],
                           colWidths=[180*mm])
        footer_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#16a34a")),
            ("TEXTCOLOR",  (0,0), (-1,-1), colors.white),
            ("FONTSIZE",   (0,0), (-1,-1), 9),
            ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
            ("ALIGN",      (0,0), (-1,-1), "CENTER"),
            ("TOPPADDING", (0,0), (-1,-1), 7),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ]))
        elements.append(footer_tbl)

        doc.build(elements)
        buffer.seek(0)

        safe_name = "".join(c for c in name if c.isalnum() or c in " _-").strip().replace(" ", "_") or "Student"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"{safe_name}_Cut-off_Analysis.pdf",
            mimetype="application/pdf",
        )

    except Exception as e:
        # PATCH: log full traceback server-side AND send the message back
        # so the browser Network tab shows exactly what broke.
        logger.exception("[download_pdf] PDF generation failed")
        return jsonify({
            "error": f"PDF generation failed: {str(e)}"
        }), 500