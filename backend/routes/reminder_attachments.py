import os
import uuid
from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename
from db import get_connection, get_cursor
from auth_utils import login_required

reminder_attachments_bp = Blueprint("reminder_attachments", __name__)

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "xlsx", "docx", "txt"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_upload_dir():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_dir = os.path.join(base_dir, "uploads", "reminder_attachments")
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


@reminder_attachments_bp.route("/reminders/events/<int:event_id>/attachments", methods=["POST"])
@login_required
def upload_attachment(event_id):
    try:
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"success": False, "error": "No file selected"}), 400

        if not allowed_file(file.filename):
            return jsonify({"success": False, "error": "File type not allowed"}), 400

        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if size > MAX_FILE_SIZE:
            return jsonify({"success": False, "error": "File too large (max 10MB)"}), 400

        original_name = secure_filename(file.filename)
        unique_name = f"{uuid.uuid4().hex}_{original_name}"
        upload_dir = get_upload_dir()
        filepath = os.path.join(upload_dir, unique_name)
        file.save(filepath)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_attachments (event_id, filename, filepath)
            VALUES (%s, %s, %s) RETURNING id
        """, (event_id, original_name, filepath))
        att_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True, "id": att_id, "filename": original_name}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@reminder_attachments_bp.route("/reminders/events/<int:event_id>/attachments", methods=["GET"])
@login_required
def get_attachments(event_id):
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT id, filename, uploaded_at FROM reminder_attachments
            WHERE event_id = %s ORDER BY uploaded_at ASC
        """, (event_id,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify({"success": True, "data": rows})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@reminder_attachments_bp.route("/reminders/attachments/<int:attachment_id>", methods=["DELETE"])
@login_required
def delete_attachment(attachment_id):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT filepath FROM reminder_attachments WHERE id = %s", (attachment_id,))
        row = cur.fetchone()
        if row:
            filepath = row[0]
            if os.path.exists(filepath):
                os.remove(filepath)
        cur.execute("DELETE FROM reminder_attachments WHERE id = %s", (attachment_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Attachment deleted"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500