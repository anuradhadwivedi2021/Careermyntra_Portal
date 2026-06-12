# routes/college_master.py — College Master Database CRUD
# Handles: Add / Edit / Delete / Search / Bulk Import colleges
# College Code is the unique key used to enrich cut-off data

from flask import Blueprint, jsonify, request
import json
from db import get_connection, get_cursor

college_master_bp = Blueprint("college_master", __name__)


# ─── GET all colleges (with optional search/filter) ───────────────────────────
@college_master_bp.route("/colleges", methods=["GET"])
def get_colleges():
    try:
        search   = request.args.get("search", "").strip()
        district = request.args.get("district", "").strip()
        college_type = request.args.get("type", "").strip()
        page     = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
        offset   = (page - 1) * per_page

        conn = get_connection()
        cur  = get_cursor(conn)

        where_clauses = []
        params = []

        if search:
            where_clauses.append(
                "(LOWER(college_code) LIKE %s OR LOWER(college_name) LIKE %s OR LOWER(city) LIKE %s)"
            )
            s = f"%{search.lower()}%"
            params += [s, s, s]

        if district:
            where_clauses.append("LOWER(district) = LOWER(%s)")
            params.append(district)

        if college_type:
            where_clauses.append("LOWER(college_type) = LOWER(%s)")
            params.append(college_type)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Count total
        cur.execute(f"SELECT COUNT(*) as cnt FROM college_master {where_sql};", params)
        total = cur.fetchone()["cnt"]

        # Fetch page
        cur.execute(
            f"SELECT * FROM college_master {where_sql} ORDER BY college_code LIMIT %s OFFSET %s;",
            params + [per_page, offset]
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "colleges": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500





# ─── POST add single college ──────────────────────────────────────────────────
@college_master_bp.route("/colleges", methods=["POST"])
def add_college():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "JSON body required"}), 400

        college_code = data.get("college_code", "").strip().upper()
        college_name = data.get("college_name", "").strip()

        if not college_code or not college_name:
            return jsonify({"success": False, "error": "college_code and college_name are required"}), 400

        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO college_master
              (college_code, college_name, district, city, university, college_type,
               management, minority_status, autonomy_status, website, phone, address, state)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id;
        """, (
            college_code, college_name,
            data.get("district", ""), data.get("city", ""),
            data.get("university", ""), data.get("college_type", ""),
            data.get("management", ""), data.get("minority_status", ""),
            data.get("autonomy_status", ""), data.get("website", ""),
            data.get("phone", ""), data.get("address", ""),
            data.get("state", "Maharashtra")
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close(); conn.close()

        return jsonify({
            "success": True,
            "message": f'College "{college_name}" added successfully!',
            "id": new_id
        }), 201

    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return jsonify({"success": False, "error": f"College code already exists"}), 409
        return jsonify({"success": False, "error": str(e)}), 500


# ─── PUT update college ───────────────────────────────────────────────────────
@college_master_bp.route("/colleges/<int:college_id>", methods=["PUT"])
def update_college(college_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "JSON body required"}), 400

        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE college_master SET
              college_code = %s, college_name = %s, district = %s, city = %s,
              university = %s, college_type = %s, management = %s,
              minority_status = %s, autonomy_status = %s,
              website = %s, phone = %s, address = %s, state = %s,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = %s;
        """, (
            data.get("college_code","").strip().upper(),
            data.get("college_name","").strip(),
            data.get("district",""), data.get("city",""),
            data.get("university",""), data.get("college_type",""),
            data.get("management",""), data.get("minority_status",""),
            data.get("autonomy_status",""), data.get("website",""),
            data.get("phone",""), data.get("address",""),
            data.get("state","Maharashtra"),
            college_id
        ))
        affected = cur.rowcount
        conn.commit()
        cur.close(); conn.close()

        if affected == 0:
            return jsonify({"success": False, "error": "College not found"}), 404
        return jsonify({"success": True, "message": "College updated successfully!"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── DELETE college ───────────────────────────────────────────────────────────
@college_master_bp.route("/colleges/<int:college_id>", methods=["DELETE"])
def delete_college(college_id):
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT college_name FROM college_master WHERE id = %s;", (college_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "College not found"}), 404

        name = row["college_name"]
        cur2 = conn.cursor()
        cur2.execute("DELETE FROM college_master WHERE id = %s;", (college_id,))
        conn.commit()
        cur.close(); cur2.close(); conn.close()
        return jsonify({"success": True, "message": f'College "{name}" deleted.'})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── POST bulk import (JSON array) ────────────────────────────────────────────
@college_master_bp.route("/colleges/bulk", methods=["POST"])
def bulk_import():
    try:
        data = request.get_json()
        colleges = data if isinstance(data, list) else data.get("colleges", [])
        if not colleges:
            return jsonify({"success": False, "error": "No colleges provided"}), 400

        conn = get_connection()
        cur  = conn.cursor()
        inserted = 0
        skipped  = 0
        errors   = []

        for idx, c in enumerate(colleges):
            code = str(c.get("college_code", "")).strip().upper()
            name = str(c.get("college_name", "")).strip()
            if not code or not name:
                errors.append(f"Row {idx+1}: missing college_code or college_name")
                skipped += 1
                continue
            try:
                cur.execute("""
                    INSERT INTO college_master
                      (college_code, college_name, district, city, university, college_type,
                       management, minority_status, autonomy_status, website, phone, address, state)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (college_code) DO UPDATE SET
                      college_name = EXCLUDED.college_name,
                      district     = EXCLUDED.district,
                      city         = EXCLUDED.city,
                      university   = EXCLUDED.university,
                      college_type = EXCLUDED.college_type,
                      management   = EXCLUDED.management,
                      updated_at   = CURRENT_TIMESTAMP;
                """, (
                    code, name,
                    c.get("district",""), c.get("city",""),
                    c.get("university",""), c.get("college_type",""),
                    c.get("management",""), c.get("minority_status",""),
                    c.get("autonomy_status",""), c.get("website",""),
                    c.get("phone",""), c.get("address",""),
                    c.get("state","Maharashtra")
                ))
                inserted += 1
            except Exception as e2:
                errors.append(f"Row {idx+1} ({code}): {str(e2)}")
                skipped += 1

        conn.commit()
        cur.close(); conn.close()

        return jsonify({
            "success": True,
            "message": f"{inserted} colleges imported/updated, {skipped} skipped.",
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors[:10]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET distinct filter options ──────────────────────────────────────────────
@college_master_bp.route("/colleges/meta/filters", methods=["GET"])
def get_filters():
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT DISTINCT district FROM college_master WHERE district != '' ORDER BY district;")
        districts = [r["district"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT college_type FROM college_master WHERE college_type != '' ORDER BY college_type;")
        types = [r["college_type"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT university FROM college_master WHERE university != '' ORDER BY university;")
        universities = [r["university"] for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({"success": True, "districts": districts, "types": types, "universities": universities})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── POST upload PDF and extract college codes/names ────────────────────────
import os
import re
import io

@college_master_bp.route("/colleges/upload-pdf", methods=["POST"])
def upload_pdf_colleges():
    """
    PDF upload karo → college codes aur names extract karo →
    college_master table mein upsert karo.
    Pattern: 5-digit code - College Name
    e.g.  "6001 - Govt. College of Engineering, Pune"
    """
    try:
        import pdfplumber
    except ImportError:
        return jsonify({"success": False, "error": "pdfplumber not installed. Run: pip install pdfplumber"}), 500

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"success": False, "error": "Only PDF files are allowed"}), 400

    # ── Read PDF in memory ──
    pdf_bytes = f.read()

    # Pattern: optional whitespace, 4-6 digit code, dash/hyphen, college name
    college_pattern = re.compile(
        r"^\s*(\d{4,6})\s*[-–—]\s*(.+)",
        re.MULTILINE
    )

    found   = {}   # code -> name (deduplicated)
    pages_scanned = 0

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_scanned = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                for m in college_pattern.finditer(text):
                    code = m.group(1).strip()
                    name = m.group(2).strip()
                    # Clean trailing garbage (page numbers, extra text after long dash)
                    name = re.split(r"\s{3,}|\t", name)[0].strip()
                    if len(name) > 5:   # skip noise
                        found[code] = name
    except Exception as e:
        return jsonify({"success": False, "error": f"PDF read error: {str(e)}"}), 500

    if not found:
        return jsonify({
            "success": False,
            "error": "No college codes found in PDF. Expected pattern: '60001 - College Name'",
            "pages_scanned": pages_scanned
        }), 422

    # ── Upsert into DB ──
    conn = get_connection()
    cur  = conn.cursor()
    inserted = 0
    skipped  = 0

    for code, name in found.items():
        try:
            cur.execute("""
                INSERT INTO college_master (college_code, college_name)
                VALUES (%s, %s)
                ON CONFLICT (college_code) DO UPDATE
                  SET college_name = EXCLUDED.college_name,
                      updated_at   = CURRENT_TIMESTAMP;
            """, (code.upper(), name))
            inserted += 1
        except Exception:
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "success":       True,
        "message":       f"{inserted} colleges extracted and saved from PDF.",
        "extracted":     inserted,
        "skipped":       skipped,
        "pages_scanned": pages_scanned,
        "preview":       [{"college_code": k, "college_name": v}
                          for k, v in list(found.items())[:10]]
    })

    # ─── GET single college by code ───────────────────────────────────────────────
@college_master_bp.route("/colleges/<code>", methods=["GET"])
def get_college(code):
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT * FROM college_master WHERE LOWER(college_code) = LOWER(%s);", (code,))
        row  = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return jsonify({"success": False, "error": "College not found"}), 404
        return jsonify({"success": True, "college": dict(row)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500