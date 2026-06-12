from flask import Blueprint, jsonify, request, current_app
import os
from db import get_connection, get_cursor

courses_bp = Blueprint("courses", __name__)


@courses_bp.route("/courses", methods=["GET"])
def get_courses():
    try:
        stream_id = request.args.get("stream_id")
        conn = get_connection()
        cur  = get_cursor(conn)
        if stream_id:
            cur.execute("SELECT * FROM courses WHERE stream_id = %s ORDER BY id;", (stream_id,))
        else:
            cur.execute("SELECT * FROM courses ORDER BY id;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "courses": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@courses_bp.route("/courses/new", methods=["GET", "POST"])
def add_course():
    if request.method == "GET":
        return jsonify({"success": True, "message": "✅ /api/courses/new route is working!"})

    course_name = request.form.get("course_name", "").strip()
    course_exam = request.form.get("course_exam", "").strip()
    course_icon = request.form.get("course_icon", "📁").strip()
    stream_id   = request.form.get("stream_id") or None

    if not course_name or not course_exam:
        return jsonify({"success": False, "error": "Course name and exam are required"}), 400

    script_file = request.files.get("script_file")
    input_file  = request.files.get("input_file")
    output_file = request.files.get("output_file")

    if not script_file or not script_file.filename.endswith(".py"):
        return jsonify({"success": False, "error": "Python script (.py) required"}), 400
    if not input_file:
        return jsonify({"success": False, "error": "Sample input file required"}), 400
    if not output_file or not output_file.filename.endswith(".xlsx"):
        return jsonify({"success": False, "error": "Sample output (.xlsx) required"}), 400

    try:
        safe_name      = course_name.lower().replace(" ", "_").replace("&", "and")
        script_name    = safe_name + ".py"
        input_ext      = os.path.splitext(input_file.filename)[1]
        input_name     = safe_name + "_sample_input" + input_ext
        output_name    = safe_name + "_sample_output.xlsx"

        # ── Script content DB mein save karo (Render filesystem ke liye) ──
        script_content = script_file.read().decode("utf-8")

        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """INSERT INTO courses
               (name, exam, icon, script, script_content, sample_input, sample_output, stream_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id;""",
            (course_name, course_exam, course_icon, script_name,
             script_content, input_name, output_name, stream_id)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        print(f"[SUCCESS] Course added — ID: {new_id}, script saved to DB ✅")
        return jsonify({
            "success": True,
            "message": f'Course "{course_name}" added!',
            "course": {
                "id": new_id, "name": course_name, "exam": course_exam,
                "icon": course_icon, "script": script_name,
                "sample_input": input_name, "sample_output": output_name
            }
        }), 201

    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@courses_bp.route("/courses/<int:course_id>", methods=["GET"])
def get_course(course_id):
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT * FROM courses WHERE id = %s;", (course_id,))
        row  = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return jsonify({"success": False, "error": "Course not found"}), 404
        return jsonify({"success": True, "course": dict(row)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@courses_bp.route("/courses/<int:course_id>", methods=["DELETE"])
def delete_course(course_id):
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT * FROM courses WHERE id = %s;", (course_id,))
        course = cur.fetchone()
        if not course:
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "Course not found"}), 404
        course = dict(course)
        cur2 = conn.cursor()
        cur2.execute("DELETE FROM courses WHERE id = %s;", (course_id,))
        conn.commit()
        cur.close(); cur2.close(); conn.close()
        return jsonify({"success": True, "message": f'Course "{course["name"]}" deleted.'})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500