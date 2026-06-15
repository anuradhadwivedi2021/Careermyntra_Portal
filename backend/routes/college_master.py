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


# ─── Async PDF processing ────────────────────────────────────────────────────
import os
import re
import io
import uuid
import threading

PDF_TASKS = {}  # task_id -> status dict

def process_pdf_background(task_id, pdf_bytes):
    """Background mein PDF process karo aur DB mein save karo."""
    try:
        import pdfplumber

        PDF_TASKS[task_id]["status"]  = "processing"
        PDF_TASKS[task_id]["message"] = "PDF scan ho raha hai..."

        college_pattern = re.compile(
            r"^\s*(\d{4,6})\s*[-–—]\s*(.+)",
            re.MULTILINE
        )

        found = {}
        pages_scanned = 0

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total_pages = len(pdf.pages)
            PDF_TASKS[task_id]["total_pages"] = total_pages

            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                for m in college_pattern.finditer(text):
                    code = m.group(1).strip()
                    name = m.group(2).strip()
                    name = re.split(r"\s{3,}|\t", name)[0].strip()
                    if len(name) > 5:
                        found[code] = name

                pages_scanned += 1
                pct = int((pages_scanned / total_pages) * 80)
                PDF_TASKS[task_id]["percent"] = pct
                PDF_TASKS[task_id]["message"] = f"Scanning page {pages_scanned}/{total_pages}..."

        if not found:
            PDF_TASKS[task_id]["status"]  = "error"
            PDF_TASKS[task_id]["message"] = "No college codes found in PDF."
            return

        # ── DB mein save karo ──
        PDF_TASKS[task_id]["message"] = "Database mein save ho raha hai..."
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

        PDF_TASKS[task_id]["status"]        = "completed"
        PDF_TASKS[task_id]["percent"]       = 100
        PDF_TASKS[task_id]["message"]       = f"{inserted} colleges saved!"
        PDF_TASKS[task_id]["extracted"]     = inserted
        PDF_TASKS[task_id]["skipped"]       = skipped
        PDF_TASKS[task_id]["pages_scanned"] = pages_scanned
        PDF_TASKS[task_id]["preview"]       = [
            {"college_code": k, "college_name": v}
            for k, v in list(found.items())[:10]
        ]

    except Exception as e:
        PDF_TASKS[task_id]["status"]  = "error"
        PDF_TASKS[task_id]["message"] = f"Error: {str(e)}"
        PDF_TASKS[task_id]["percent"] = 0


@college_master_bp.route("/colleges/upload-pdf", methods=["POST"])
def upload_pdf_colleges():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"success": False, "error": "Only PDF files allowed"}), 400

    pdf_bytes = f.read()
    task_id   = str(uuid.uuid4())

    PDF_TASKS[task_id] = {
        "status":  "pending",
        "percent": 0,
        "message": "Processing shuru ho rahi hai...",
        "total_pages": 0
    }

    thread = threading.Thread(
        target=process_pdf_background,
        args=(task_id, pdf_bytes),
        daemon=True
    )
    thread.start()

    return jsonify({"success": True, "task_id": task_id})




# ─── POST Excel upload ────────────────────────────────────────────────────────
@college_master_bp.route("/colleges/upload-excel", methods=["POST"])
def upload_excel_colleges():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not (f.filename.lower().endswith(".xlsx") or f.filename.lower().endswith(".xls")):
        return jsonify({"success": False, "error": "Only .xlsx or .xls files allowed"}), 400

    try:
        import pandas as pd
        df = pd.read_excel(f, dtype=str)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        if "college_code" not in df.columns or "college_name" not in df.columns:
            return jsonify({"success": False, "error": "Excel mein 'college_code' aur 'college_name' columns hone chahiye"}), 400

        df = df.fillna("")
        conn = get_connection()
        cur  = conn.cursor()
        inserted = 0
        skipped  = 0
        errors   = []

        for idx, row in df.iterrows():
            code = str(row.get("college_code", "")).strip().upper()
            name = str(row.get("college_name", "")).strip()
            if not code or not name:
                errors.append(f"Row {idx+2}: college_code ya college_name missing")
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
                    str(row.get("district", "")),
                    str(row.get("city", "")),
                    str(row.get("university", "")),
                    str(row.get("college_type", "")),
                    str(row.get("management", "")),
                    str(row.get("minority_status", "")),
                    str(row.get("autonomy_status", "")),
                    str(row.get("website", "")),
                    str(row.get("phone", "")),
                    str(row.get("address", "")),
                    str(row.get("state", "Maharashtra"))
                ))
                inserted += 1
            except Exception as e2:
                errors.append(f"Row {idx+2} ({code}): {str(e2)}")
                skipped += 1

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": f"{inserted} colleges imported/updated, {skipped} skipped.",
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors[:10]
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500