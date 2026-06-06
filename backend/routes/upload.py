# routes/upload.py — File upload & processing with live progress

from flask import Blueprint, jsonify, request, current_app
import os
import uuid
import threading
import importlib.util
import time

upload_bp = Blueprint("upload", __name__)

# ─── In-memory task store ─────────────────────────────────────
# { task_id: { percent, message, status, output_file } }
TASKS = {}

ALLOWED_EXTENSIONS = {".pdf", ".xls", ".xlsx"}

def allowed_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS

# ─── POST /api/upload ─────────────────────────────────────────
@upload_bp.route("/upload", methods=["POST"])
def upload_file():
    """
    Upload a file for processing.
    Form data: course_name (str), file (file)
    Returns: task_id to track progress
    """
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

    # Save uploaded file
    upload_dir = current_app.config["UPLOAD_DIR"]
    task_id    = str(uuid.uuid4())
    ext        = os.path.splitext(file.filename)[1].lower()
    saved_name = f"{task_id}{ext}"
    saved_path = os.path.join(upload_dir, saved_name)
    file.save(saved_path)

    # Output path
    output_dir  = current_app.config["OUTPUT_DIR"]
    output_name = f"{course_name.lower().replace(' ', '_')}_{task_id[:8]}_output.xlsx"
    output_path = os.path.join(output_dir, output_name)

    # Init task
    TASKS[task_id] = {
        "percent":     0,
        "message":     "Queued...",
        "status":      "pending",
        "output_file": output_name,
        "course":      course_name
    }

    # Run processing in background thread
    scripts_dir = current_app.config["SCRIPTS_DIR"]
    thread = threading.Thread(
        target=run_processing,
        args=(task_id, course_name, saved_path, output_path, scripts_dir),
        daemon=True
    )
    thread.start()

    return jsonify({
        "success": True,
        "task_id": task_id,
        "message": "File uploaded! Processing started.",
        "output_file": output_name
    })


# ─── Background Processing ────────────────────────────────────
def run_processing(task_id, course_name, pdf_path, output_path, scripts_dir):
    """Run the course-specific Python script in a background thread."""

    def update(percent, message):
        TASKS[task_id]["percent"] = percent
        TASKS[task_id]["message"] = message
        print(f"[Task {task_id[:8]}] [{percent}%] {message}")

    try:
        TASKS[task_id]["status"] = "processing"
        update(5, "Uploading file...")

        # ── Find script for this course ──
        script_name = course_name.lower().replace(" ", "_").replace("&", "and") + ".py"
        script_path = os.path.join(scripts_dir, script_name)

        # ── Fallback to universal.py if course script not found ──
        if not os.path.exists(script_path):
            universal_path = os.path.join(scripts_dir, "universal.py")
            if os.path.exists(universal_path):
                print(f"[Task {task_id[:8]}] Script '{script_name}' not found — using universal.py")
                script_path = universal_path
            else:
                # Last resort fallback to engineering.py
                script_path = os.path.join(scripts_dir, "engineering.py")
                print(f"[Task {task_id[:8]}] universal.py not found — falling back to engineering.py")

        update(10, "Loading processing script...")

        # Dynamically import the course script
        spec   = importlib.util.spec_from_file_location("course_script", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        update(15, "Reading file...")

        # Call process() from the script
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


# ─── GET /api/progress/<task_id> ─────────────────────────────
@upload_bp.route("/progress/<task_id>", methods=["GET"])
def get_progress(task_id):
    """
    Poll this endpoint to get live processing progress.
    Returns: percent, message, status, output_file
    """
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

