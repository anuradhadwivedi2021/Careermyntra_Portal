# routes/streams.py — Stream Master CRUD + Course-Stream Mapping
# Handles: Add / Edit / Delete streams, map courses to streams, grouped view
#
# Register in main.py:
#   from routes.streams import streams_bp
#   app.register_blueprint(streams_bp, url_prefix="/api")
#
# DB Migration (run once):
#   psql -U postgres -d careermyntra_portal -f migrations/add_streams.sql

from flask import Blueprint, jsonify, request
from db import get_connection, get_cursor
from auth_utils import login_required

streams_bp = Blueprint("streams", __name__)


# ═══════════════════════════════════════════════════════════════
#  HELPER
# ═══════════════════════════════════════════════════════════════
def _ensure_streams_table(conn):
    """Create tables if migration hasn't been run yet (safety net)."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS streams (
            id          SERIAL PRIMARY KEY,
            name        VARCHAR(100) NOT NULL UNIQUE,
            icon        VARCHAR(10)  DEFAULT '📚',
            description TEXT,
            color       VARCHAR(20)  DEFAULT '#1565c0',
            created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        ALTER TABLE courses ADD COLUMN IF NOT EXISTS stream_id INT REFERENCES streams(id) ON DELETE SET NULL;
    """)
    conn.commit()
    cur.close()


# ═══════════════════════════════════════════════════════════════
#  STREAMS CRUD
# ═══════════════════════════════════════════════════════════════

# ── GET /api/streams  ────────────────────────────────────────
@streams_bp.route("/streams", methods=["GET"])
@login_required
def get_streams():
    """Return all streams.  ?with_courses=true nests courses inside each stream."""
    try:
        with_courses = request.args.get("with_courses", "false").lower() == "true"
        search       = request.args.get("search", "").strip()

        conn = get_connection()
        _ensure_streams_table(conn)
        cur  = get_cursor(conn)

        if search:
            cur.execute(
                "SELECT * FROM streams WHERE LOWER(name) LIKE %s ORDER BY name;",
                (f"%{search.lower()}%",)
            )
        else:
            cur.execute("SELECT * FROM streams ORDER BY name;")

        streams = [dict(r) for r in cur.fetchall()]

        if with_courses:
            for s in streams:
                cur.execute(
                    """SELECT id, name, exam, icon, created_at
                       FROM courses WHERE stream_id = %s ORDER BY name;""",
                    (s["id"],)
                )
                s["courses"] = [dict(c) for c in cur.fetchall()]
                s["course_count"] = len(s["courses"])
        else:
            # Just return count
            for s in streams:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM courses WHERE stream_id = %s;",
                    (s["id"],)
                )
                s["course_count"] = cur.fetchone()["cnt"]

        # Stats
        cur.execute("SELECT COUNT(*) AS cnt FROM courses;")
        total_courses = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM courses WHERE stream_id IS NOT NULL;")
        mapped_courses = cur.fetchone()["cnt"]

        cur.close(); conn.close()

        return jsonify({
            "success": True,
            "streams": streams,
            "total_streams": len(streams),
            "total_courses": total_courses,
            "mapped_courses": mapped_courses,
            "unmapped_courses": total_courses - mapped_courses
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /api/streams/grouped  ────────────────────────────────
@streams_bp.route("/streams/grouped", methods=["GET"])
@login_required
def get_grouped():
    """Return courses grouped by stream — perfect for dropdowns & reports."""
    try:
        conn = get_connection()
        _ensure_streams_table(conn)
        cur  = get_cursor(conn)

        cur.execute("SELECT * FROM streams ORDER BY name;")
        streams = [dict(r) for r in cur.fetchall()]

        result = []
        for s in streams:
            cur.execute(
                "SELECT id, name, exam, icon FROM courses WHERE stream_id = %s ORDER BY name;",
                (s["id"],)
            )
            courses = [dict(c) for c in cur.fetchall()]
            result.append({
                "stream_id":    s["id"],
                "stream_name":  s["name"],
                "stream_icon":  s["icon"],
                "stream_color": s["color"],
                "courses":      courses,
                "count":        len(courses)
            })

        # Unmapped courses
        cur.execute(
            "SELECT id, name, exam, icon FROM courses WHERE stream_id IS NULL ORDER BY name;"
        )
        unmapped = [dict(c) for c in cur.fetchall()]

        cur.close(); conn.close()

        return jsonify({
            "success":  True,
            "grouped":  result,
            "unmapped": unmapped
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /api/streams/<id>  ───────────────────────────────────
@streams_bp.route("/streams/<int:stream_id>", methods=["GET"])
@login_required
def get_stream(stream_id):
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT * FROM streams WHERE id = %s;", (stream_id,))
        row  = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "Stream not found"}), 404
        s = dict(row)
        cur.execute(
            "SELECT id, name, exam, icon FROM courses WHERE stream_id = %s ORDER BY name;",
            (stream_id,)
        )
        s["courses"] = [dict(c) for c in cur.fetchall()]
        s["course_count"] = len(s["courses"])
        cur.close(); conn.close()
        return jsonify({"success": True, "stream": s})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── POST /api/streams  ───────────────────────────────────────
@streams_bp.route("/streams", methods=["POST"])
@login_required
def add_stream():
    """Create a new stream. Body: { name, icon, description, color }"""
    try:
        data        = request.get_json(force=True) or {}
        name        = data.get("name", "").strip()
        icon        = data.get("icon", "📚").strip()
        description = data.get("description", "").strip()
        color       = data.get("color", "#1565c0").strip()

        if not name:
            return jsonify({"success": False, "error": "Stream name is required"}), 400

        conn = get_connection()
        _ensure_streams_table(conn)
        cur  = conn.cursor()

        cur.execute(
            """INSERT INTO streams (name, icon, description, color)
               VALUES (%s, %s, %s, %s) RETURNING id;""",
            (name, icon, description, color)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close(); conn.close()

        print(f"[STREAM] Created — ID:{new_id}, Name:{name} ✅")
        return jsonify({
            "success": True,
            "message": f'Stream "{name}" created successfully!',
            "stream": {"id": new_id, "name": name, "icon": icon,
                       "description": description, "color": color, "course_count": 0}
        }), 201

    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return jsonify({"success": False, "error": f'Stream "{name}" already exists'}), 409
        return jsonify({"success": False, "error": str(e)}), 500


# ── PUT /api/streams/<id>  ───────────────────────────────────
@streams_bp.route("/streams/<int:stream_id>", methods=["PUT"])
@login_required
def edit_stream(stream_id):
    """Update stream details. Body: { name, icon, description, color }"""
    try:
        data        = request.get_json(force=True) or {}
        name        = data.get("name", "").strip()
        icon        = data.get("icon", "📚").strip()
        description = data.get("description", "").strip()
        color       = data.get("color", "#1565c0").strip()

        if not name:
            return jsonify({"success": False, "error": "Stream name is required"}), 400

        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT id FROM streams WHERE id = %s;", (stream_id,))
        if not cur.fetchone():
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "Stream not found"}), 404

        cur2 = conn.cursor()
        cur2.execute(
            """UPDATE streams
               SET name=%s, icon=%s, description=%s, color=%s, updated_at=CURRENT_TIMESTAMP
               WHERE id=%s;""",
            (name, icon, description, color, stream_id)
        )
        conn.commit()
        cur.close(); cur2.close(); conn.close()

        return jsonify({
            "success": True,
            "message": f'Stream "{name}" updated!',
            "stream": {"id": stream_id, "name": name, "icon": icon,
                       "description": description, "color": color}
        })

    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return jsonify({"success": False, "error": f'Stream name already taken'}), 409
        return jsonify({"success": False, "error": str(e)}), 500


# ── DELETE /api/streams/<id>  ────────────────────────────────
@streams_bp.route("/streams/<int:stream_id>", methods=["DELETE"])
@login_required
def delete_stream(stream_id):
    """Delete stream. Courses mapped to it will have stream_id set to NULL."""
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT * FROM streams WHERE id = %s;", (stream_id,))
        row  = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "Stream not found"}), 404

        name = dict(row)["name"]
        cur2 = conn.cursor()
        cur2.execute("DELETE FROM streams WHERE id = %s;", (stream_id,))
        conn.commit()
        cur.close(); cur2.close(); conn.close()

        return jsonify({"success": True, "message": f'Stream "{name}" deleted. Courses unlinked.'})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  COURSE ↔ STREAM MAPPING
# ═══════════════════════════════════════════════════════════════

# ── POST /api/courses/<id>/stream  ──────────────────────────
@streams_bp.route("/courses/<int:course_id>/stream", methods=["POST"])
@login_required
def assign_course_stream(course_id):
    """Assign (or unassign) a course to a stream.
       Body: { stream_id: 3 }  OR  { stream_id: null } to unassign.
    """
    try:
        data      = request.get_json(force=True) or {}
        stream_id = data.get("stream_id")   # None = unassign

        conn = get_connection()
        cur  = get_cursor(conn)

        cur.execute("SELECT * FROM courses WHERE id = %s;", (course_id,))
        course = cur.fetchone()
        if not course:
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "Course not found"}), 404

        if stream_id is not None:
            cur.execute("SELECT id FROM streams WHERE id = %s;", (stream_id,))
            if not cur.fetchone():
                cur.close(); conn.close()
                return jsonify({"success": False, "error": "Stream not found"}), 404

        cur2 = conn.cursor()
        cur2.execute(
            "UPDATE courses SET stream_id = %s WHERE id = %s;",
            (stream_id, course_id)
        )
        conn.commit()
        cur.close(); cur2.close(); conn.close()

        action = f"assigned to stream #{stream_id}" if stream_id else "unassigned"
        return jsonify({
            "success": True,
            "message": f'Course "{dict(course)["name"]}" {action} ✅'
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── POST /api/streams/<id>/courses/bulk  ────────────────────
@streams_bp.route("/streams/<int:stream_id>/courses/bulk", methods=["POST"])
@login_required
def bulk_assign(stream_id):
    """Bulk assign multiple courses to a stream.
       Body: { course_ids: [1, 2, 3] }
    """
    try:
        data       = request.get_json(force=True) or {}
        course_ids = data.get("course_ids", [])

        if not course_ids:
            return jsonify({"success": False, "error": "No course_ids provided"}), 400

        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT id FROM streams WHERE id = %s;", (stream_id,))
        if not cur.fetchone():
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "Stream not found"}), 404

        cur2 = conn.cursor()
        cur2.executemany(
            "UPDATE courses SET stream_id = %s WHERE id = %s;",
            [(stream_id, cid) for cid in course_ids]
        )
        conn.commit()
        cur.close(); cur2.close(); conn.close()

        return jsonify({
            "success": True,
            "message": f"{len(course_ids)} course(s) assigned to stream #{stream_id} ✅",
            "updated": len(course_ids)
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /api/streams/<id>/courses  ──────────────────────────
@streams_bp.route("/streams/<int:stream_id>/courses", methods=["GET"])
@login_required
def get_stream_courses(stream_id):
    """Get all courses belonging to a stream."""
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT * FROM streams WHERE id = %s;", (stream_id,))
        stream = cur.fetchone()
        if not stream:
            cur.close(); conn.close()
            return jsonify({"success": False, "error": "Stream not found"}), 404
        cur.execute(
            "SELECT id, name, exam, icon, created_at FROM courses WHERE stream_id = %s ORDER BY name;",
            (stream_id,)
        )
        courses = [dict(c) for c in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({
            "success": True,
            "stream": dict(stream),
            "courses": courses,
            "total": len(courses)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /api/courses/unmapped  ──────────────────────────────
@streams_bp.route("/courses/unmapped", methods=["GET"])
@login_required
def get_unmapped_courses():
    """Get all courses not yet assigned to any stream."""
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute(
            "SELECT id, name, exam, icon FROM courses WHERE stream_id IS NULL ORDER BY name;"
        )
        courses = [dict(c) for c in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({"success": True, "courses": courses, "total": len(courses)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500