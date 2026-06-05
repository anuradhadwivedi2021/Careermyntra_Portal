# routes/courses.py — PostgreSQL version

from flask import Blueprint, jsonify, request, current_app
import os
from db import get_connection, get_cursor

courses_bp = Blueprint("courses", __name__)


@courses_bp.route("/courses", methods=["GET"])
def get_courses():
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT * FROM courses ORDER BY id;")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "courses": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ✅ /courses/new — renamed from /courses/add to avoid Flask routing conflict
@courses_bp.route("/courses/new", methods=["GET", "POST"])
def add_course():
    if request.method == "GET":
        return jsonify({"success": True, "message": "✅ /api/courses/new route is working!"})

    print("\n" + "="*60)
    course_name = request.form.get("course_name", "").strip()
    course_exam = request.form.get("course_exam", "").strip()
    course_icon = request.form.get("course_icon", "📁").strip()

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
        scripts_dir = current_app.config["SCRIPTS_DIR"]
        samples_dir = os.path.join(current_app.config["UPLOAD_DIR"], "samples")
        os.makedirs(scripts_dir, exist_ok=True)
        os.makedirs(samples_dir, exist_ok=True)

        safe_name   = course_name.lower().replace(" ", "_").replace("&", "and")
        script_name = safe_name + ".py"
        input_ext   = os.path.splitext(input_file.filename)[1]
        input_name  = safe_name + "_sample_input" + input_ext
        output_name = safe_name + "_sample_output.xlsx"

        script_file.save(os.path.join(scripts_dir, script_name))
        input_file.save(os.path.join(samples_dir, input_name))
        output_file.save(os.path.join(samples_dir, output_name))

        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "INSERT INTO courses (name, exam, icon, script, sample_input, sample_output) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id;",
            (course_name, course_exam, course_icon, script_name, input_name, output_name)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        print(f"[SUCCESS] Course added — ID: {new_id}")
        return jsonify({"success": True, "message": f'Course "{course_name}" added!',
            "course": {"id": new_id, "name": course_name, "exam": course_exam,
                       "icon": course_icon, "script": script_name,
                       "sample_input": input_name, "sample_output": output_name}}), 201

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