from flask import Blueprint, jsonify, request, current_app
import os
import uuid
import threading
import importlib.util
import tempfile
from db import get_connection, get_cursor

upload_bp = Blueprint("upload", __name__)

TASKS = {}
ALLOWED_EXTENSIONS = {".pdf", ".xls", ".xlsx"}

def allowed_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


@upload_bp.route("/upload", methods=["POST"])
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
    output_name = f"{course_name.lower().replace(' ', '_')}_{task_id[:8]}_output.xlsx"
    output_path = os.path.join(output_dir, output_name)

    TASKS[task_id] = {
        "percent":     0,
        "message":     "Queued...",
        "status":      "pending",
        "output_file": output_name,
        "course":      course_name
    }

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
        TASKS[task_id]["percent"] = percent
        TASKS[task_id]["message"] = message
        print(f"[Task {task_id[:8]}] [{percent}%] {message}")

    temp_script_path = None

    try:
        TASKS[task_id]["status"] = "processing"
        update(5, "Uploading file...")

        script_name = course_name.lower().replace(" ", "_").replace("&", "and") + ".py"
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

        update(15, "Reading file...")

        result = module.process(pdf_path, output_path, progress_callback=update)

        if result["success"]:
            TASKS[task_id]["status"]  = "completed"
            TASKS[task_id]["percent"] = 100
            TASKS[task_id]["message"] = f"Done! {result['records']} records extracted."
            TASKS[task_id]["records"] = result["records"]
        else:
            TASKS[task_id]["status"]  = "error"
            TASKS[task_id]["message"] = result["error"]

    except Exception as e:
        TASKS[task_id]["status"]  = "error"
        TASKS[task_id]["message"] = f"Processing failed: {str(e)}"
        TASKS[task_id]["percent"] = 0
        print(f"[ERROR] Task {task_id}: {e}")

    finally:
        # Temp file clean up
        if temp_script_path and os.path.exists(temp_script_path):
            os.unlink(temp_script_path)


@upload_bp.route("/progress/<task_id>", methods=["GET"])
def get_progress(task_id):
    task = TASKS.get(task_id)
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