# routes/college_predictor_dynamic_filters.py — Dynamic Filtering Patch
#
# NEW FILE — does NOT modify college_predictor.py or any existing file.
# Register this blueprint in app.py alongside the existing college_predictor_bp.
#
# Adds DB-driven cascading filter routes:
#   University        -> Districts
#   Districts         -> Category (existing /branches route already does Districts -> Branch)
#   Districts + Univ  -> Category
#   Category (+ any prior filters) -> Gender
#
# PATCH (course-aware): every route below now accepts ?course_slug=...
# (defaults to "be_btech") and resolves the correct per-course table via
# _get_table_name() from routes.college_predictor, instead of being
# hardcoded to `cap_cutoff_data`. This mirrors the same fix applied to
# college_predictor.py, so all dynamic filtering also reads from the
# course's own dedicated table (e.g. predictor_data_be_btech).

from flask import Blueprint, request, jsonify
from db import get_connection, get_cursor
from logger_setup import get_logger
from routes.college_predictor import _get_table_name  # PATCH: shared helper

logger = get_logger(__name__)
college_predictor_filters_bp = Blueprint("college_predictor_filters", __name__)


def _parse_list(param_name):
    """Read a comma-separated query param into a clean list of strings."""
    raw = request.args.get(param_name, "")
    return [v.strip() for v in raw.split(",") if v.strip()]


# ─── University → Districts ──────────────────────────────────
# GET /college-predictor/districts-by-university?universities=Savitribai Phule Pune University&course_slug=be_btech
@college_predictor_filters_bp.route("/college-predictor/districts-by-university", methods=["GET"])
def districts_by_university():
    course_slug = request.args.get("course_slug", "be_btech")  # PATCH
    table_name = _get_table_name(course_slug)                  # PATCH
    if not table_name:                                         # PATCH
        return jsonify([])                                      # PATCH

    universities = _parse_list("universities")

    conn = get_connection()
    cur = get_cursor(conn)

    if universities:
        placeholders = ",".join(["%s"] * len(universities))
        cur.execute(f"""
            SELECT DISTINCT TRIM(district) AS district
            FROM {table_name}
            WHERE university IN ({placeholders})
              AND district IS NOT NULL AND TRIM(district) != ''
            ORDER BY TRIM(district)
        """, universities)
    else:
        # No university selected -> return all districts (same as /districts)
        cur.execute(f"""
            SELECT DISTINCT TRIM(district) AS district
            FROM {table_name}
            WHERE district IS NOT NULL AND TRIM(district) != ''
            ORDER BY TRIM(district)
        """)

    rows = [r["district"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── Districts (+ optional University) → Category ────────────
# GET /college-predictor/categories-by-filters?districts=Pune,Nashik&universities=X&course_slug=be_btech
@college_predictor_filters_bp.route("/college-predictor/categories-by-filters", methods=["GET"])
def categories_by_filters():
    course_slug = request.args.get("course_slug", "be_btech")  # PATCH
    table_name = _get_table_name(course_slug)                  # PATCH
    if not table_name:                                         # PATCH
        return jsonify([])                                      # PATCH

    districts = _parse_list("districts")
    universities = _parse_list("universities")

    where = ["category IS NOT NULL"]
    params = []

    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        where.append(f"TRIM(district) IN ({placeholders})")
        params.extend(districts)

    if universities:
        placeholders = ",".join(["%s"] * len(universities))
        where.append(f"university IN ({placeholders})")
        params.extend(universities)

    where_sql = " AND ".join(where)

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"""
        SELECT DISTINCT category
        FROM {table_name}
        WHERE {where_sql}
        ORDER BY category
    """, params)
    rows = [r["category"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── Category (+ optional Districts/University/Branches) → Gender ─
# GET /college-predictor/genders-by-filters?category=OPEN&districts=Pune&universities=X&branches=Y&course_slug=be_btech
@college_predictor_filters_bp.route("/college-predictor/genders-by-filters", methods=["GET"])
def genders_by_filters():
    course_slug = request.args.get("course_slug", "be_btech")  # PATCH
    table_name = _get_table_name(course_slug)                  # PATCH
    if not table_name:                                         # PATCH
        return jsonify([])                                      # PATCH

    category = request.args.get("category", "").strip()
    districts = _parse_list("districts")
    universities = _parse_list("universities")
    branches = _parse_list("branches")

    where = ["gender IS NOT NULL"]
    params = []

    if category:
        where.append("category = %s")
        params.append(category)

    if districts:
        placeholders = ",".join(["%s"] * len(districts))
        where.append(f"TRIM(district) IN ({placeholders})")
        params.extend(districts)

    if universities:
        placeholders = ",".join(["%s"] * len(universities))
        where.append(f"university IN ({placeholders})")
        params.extend(universities)

    if branches:
        placeholders = ",".join(["%s"] * len(branches))
        where.append(f"branch_name IN ({placeholders})")
        params.extend(branches)

    where_sql = " AND ".join(where)

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"""
        SELECT DISTINCT gender
        FROM {table_name}
        WHERE {where_sql}
        ORDER BY gender
    """, params)
    rows = [r["gender"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(rows)


# ─── Generic combo endpoint: everything filters everything ───
# GET /college-predictor/cascading-options?districts=..&universities=..&category=..&branches=..&gender=..&cap_year=..&cap_round=..&course_slug=be_btech
# Returns ALL dropdown option sets in one call, each filtered by every
# OTHER currently-selected field. Use this if you want one network call
# per form change instead of five separate ones.
@college_predictor_filters_bp.route("/college-predictor/cascading-options", methods=["GET"])
def cascading_options():
    course_slug = request.args.get("course_slug", "be_btech")  # PATCH
    table_name = _get_table_name(course_slug)                  # PATCH
    if not table_name:                                         # PATCH
        return jsonify({                                        # PATCH
            "districts": [], "universities": [], "branches": [],
            "categories": [], "genders": [], "cap_years": [],
            "cap_rounds": [], "quotas": [],
        })

    districts    = _parse_list("districts")
    universities = _parse_list("universities")
    branches     = _parse_list("branches")
    category     = request.args.get("category", "").strip()
    gender       = request.args.get("gender", "").strip()
    cap_year     = request.args.get("cap_year", "").strip()
    cap_round    = request.args.get("cap_round", "").strip()
    quota        = request.args.get("quota", "").strip()

    def build_where(exclude):
        """Build WHERE clause using every filter EXCEPT the one field
        currently being computed (so a field never filters itself out)."""
        where = []
        params = []
        if districts and exclude != "districts":
            placeholders = ",".join(["%s"] * len(districts))
            where.append(f"TRIM(district) IN ({placeholders})")
            params.extend(districts)
        if universities and exclude != "universities":
            placeholders = ",".join(["%s"] * len(universities))
            where.append(f"university IN ({placeholders})")
            params.extend(universities)
        if branches and exclude != "branches":
            placeholders = ",".join(["%s"] * len(branches))
            where.append(f"branch_name IN ({placeholders})")
            params.extend(branches)
        if category and exclude != "category":
            where.append("category = %s")
            params.append(category)
        if gender and exclude != "gender":
            where.append("gender = %s")
            params.append(gender)
        if cap_year and exclude != "cap_year":
            where.append("cap_year = %s")
            params.append(cap_year)
        if cap_round and exclude != "cap_round":
            where.append("cap_round = %s")
            params.append(cap_round)
        if quota and exclude != "quota":
            where.append("quota_code = %s")
            params.append(quota)
        return (" AND ".join(where) if where else "1=1"), params

    conn = get_connection()
    cur = get_cursor(conn)

    result = {}

    field_map = [
        ("districts",    "TRIM(district)", "district IS NOT NULL AND TRIM(district) != ''"),
        # PATCH: normalize case+whitespace to avoid duplicate university entries
        ("universities",  "TRIM(university)", "university IS NOT NULL AND TRIM(university) != ''"),
        ("branches",      "branch_name",    "branch_name IS NOT NULL"),
        ("categories",    "category",       "category IS NOT NULL"),
        ("genders",       "gender",         "gender IS NOT NULL"),
        ("cap_years",     "cap_year",       "cap_year IS NOT NULL"),
        ("cap_rounds",    "cap_round",      "cap_round IS NOT NULL"),
        ("quotas",        "quota_code",     "quota_code IS NOT NULL AND TRIM(quota_code) != ''"),
    ]

    exclude_key_map = {
        "districts": "districts", "universities": "universities",
        "branches": "branches", "categories": "category",
        "genders": "gender", "cap_years": "cap_year", "cap_rounds": "cap_round",
        "quotas": "quota",
    }

    for out_key, col, not_null_clause in field_map:
        exclude = exclude_key_map[out_key]
        where_sql, params = build_where(exclude)
        cur.execute(f"""
            SELECT DISTINCT {col} AS val
            FROM {table_name}
            WHERE {not_null_clause} AND ({where_sql})
            ORDER BY {col}
        """, params)
        result[out_key] = [r["val"] for r in cur.fetchall()]

    cur.close()
    conn.close()
    return jsonify(result)