from flask import Blueprint, jsonify, request, current_app
import os
import uuid
import threading
import importlib.util
import tempfile
from db import get_connection, get_cursor
from auth_utils import login_required

from flask import Blueprint, jsonify, request, current_app

upload_bp = Blueprint("upload", __name__)

ALLOWED_EXTENSIONS = {".pdf", ".xls", ".xlsx"}

# ─── DB-backed task helpers (fixes in-memory loss on VPS restart) ────────────

def _ensure_tasks_table():
    """Create tasks table if not exists — called once on first use."""
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processing_tasks (
                task_id     TEXT PRIMARY KEY,
                percent     INTEGER DEFAULT 0,
                message     TEXT    DEFAULT 'Queued...',
                status      TEXT    DEFAULT 'pending',
                output_file TEXT,
                course      TEXT,
                records     INTEGER DEFAULT 0,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] tasks table ensure error: {e}")

def task_create(task_id, output_file, course):
    _ensure_tasks_table()
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute(
        "INSERT INTO processing_tasks (task_id, output_file, course) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;",
        (task_id, output_file, course)
    )
    conn.commit(); cur.close(); conn.close()

def task_update(task_id, percent=None, message=None, status=None, records=None):
    try:
        fields, vals = [], []
        if percent  is not None: fields.append("percent = %s");  vals.append(percent)
        if message  is not None: fields.append("message = %s");  vals.append(message)
        if status   is not None: fields.append("status = %s");   vals.append(status)
        if records  is not None: fields.append("records = %s");  vals.append(records)
        if not fields: return
        vals.append(task_id)
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute(f"UPDATE processing_tasks SET {', '.join(fields)} WHERE task_id = %s;", vals)
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"[DB] task_update error: {e}")

def task_get(task_id):
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("SELECT * FROM processing_tasks WHERE task_id = %s;", (task_id,))
        row = cur.fetchone(); cur.close(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[DB] task_get error: {e}")
        return None

def allowed_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


@upload_bp.route("/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file        = request.files["file"]
    course_name = request.form.get("course_name", "").strip()

    if not file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400
    if not allowed_file(file.filename):
        return jsonify({"success": False, "error": "Only PDF, XLS, XLSX allowed"}), 400
    if not course_name:
        return jsonify({"success": False, "error": "Course name required"}), 400

    upload_dir = current_app.config["UPLOAD_DIR"]
    task_id    = str(uuid.uuid4())
    ext        = os.path.splitext(file.filename)[1].lower()
    saved_name = f"{task_id}{ext}"
    saved_path = os.path.join(upload_dir, saved_name)
    file.save(saved_path)

    output_dir  = current_app.config["OUTPUT_DIR"]
    os.makedirs(output_dir, exist_ok=True)
    import re as _re2
    _safe = _re2.sub(r"[^a-z0-9]+", "_", course_name.lower().replace("&", "and")).strip("_")
    output_name = f"{_safe}_{task_id[:8]}_output.xlsx"
    output_path = os.path.join(output_dir, output_name)

    task_create(task_id, output_name, course_name)

    scripts_dir = current_app.config["SCRIPTS_DIR"]
    thread = threading.Thread(
        target=run_processing,
        args=(task_id, course_name, saved_path, output_path, scripts_dir),
        daemon=True
    )
    thread.start()

    return jsonify({
        "success":     True,
        "task_id":     task_id,
        "message":     "File uploaded! Processing started.",
        "output_file": output_name
    })


def get_script_from_db(course_name):
    """DB se script content nikalo — Render filesystem reset hone pe bhi kaam karega."""
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute(
            "SELECT script_content FROM courses WHERE LOWER(name) = LOWER(%s);",
            (course_name,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row["script_content"]:
            return row["script_content"]
    except Exception as e:
        print(f"[DB] Script fetch error: {e}")
    return None


def run_processing(task_id, course_name, pdf_path, output_path, scripts_dir):

    def update(percent, message):
        task_update(task_id, percent=percent, message=message)
        print(f"[Task {task_id[:8]}] [{percent}%] {message}")

    temp_script_path = None

    try:
        task_update(task_id, status="processing")
        update(5, "Uploading file...")

        import re as _re
        _sname = course_name.lower().replace("&", "and")
        _sname = _re.sub(r"[^a-z0-9]+", "_", _sname)
        script_name = _sname.strip("_") + ".py"
        script_path = os.path.join(scripts_dir, script_name)

        # ── Step 1: Disk pe check karo (local dev ke liye) ──
        if os.path.exists(script_path):
            print(f"[Task {task_id[:8]}] Script found on disk: {script_path}")

        else:
            # ── Step 2: DB se script nikalo (Render ke liye) ──
            print(f"[Task {task_id[:8]}] Disk pe script nahi mila, DB se load kar raha hoon...")
            script_content = get_script_from_db(course_name)

            if script_content:
                # Temp file mein likhkar use karo
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                )
                tmp.write(script_content)
                tmp.close()
                script_path      = tmp.name
                temp_script_path = tmp.name
                print(f"[Task {task_id[:8]}] Script DB se load hua ✅ → {script_path}")

            else:
                # ── Step 3: Fallback — engineering.py ──
                fallback = os.path.join(scripts_dir, "engineering.py")
                if os.path.exists(fallback):
                    script_path = fallback
                    print(f"[Task {task_id[:8]}] Fallback: engineering.py")
                else:
                    raise Exception(f"Script '{script_name}' nahi mila — DB mein bhi nahi hai!")

        update(10, "Loading processing script...")

        spec   = importlib.util.spec_from_file_location("course_script", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # ── Verify process() function exist karta hai ──
        if not hasattr(module, "process"):
            print(f"[Task {task_id[:8]}] ⚠️ Script mein 'process' function nahi hai! engineering.py pe fallback...")
            fallback = os.path.join(scripts_dir, "engineering.py")
            if os.path.exists(fallback):
                spec   = importlib.util.spec_from_file_location("course_script", fallback)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                print(f"[Task {task_id[:8]}] ✅ engineering.py se process() load hua")
            else:
                raise Exception("Script mein 'process' function nahi mila aur engineering.py bhi nahi hai!")

        update(15, "Reading file...")

        result = module.process(pdf_path, output_path, progress_callback=update)

        if result["success"]:
            task_update(task_id, status="completed", percent=100,
                        message=f"Done! {result['records']} records extracted.",
                        records=result["records"])
        else:
            task_update(task_id, status="error", message=result["error"])

    except Exception as e:
        task_update(task_id, status="error", message=f"Processing failed: {str(e)}", percent=0)
        print(f"[ERROR] Task {task_id}: {e}")

    finally:
        # Temp file clean up
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)


@upload_bp.route("/progress/<task_id>", methods=["GET"])

def get_progress(task_id):
    task = task_get(task_id)
    if not task:
        return jsonify({"success": False, "error": "Task not found"}), 404

    return jsonify({
        "success":     True,
        "task_id":     task_id,
        "percent":     task["percent"],
        "message":     task["message"],
        "status":      task["status"],
        "output_file": task.get("output_file"),
        "records":     task.get("records", 0)
    })