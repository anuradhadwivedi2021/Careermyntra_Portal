# routes/download.py — Download generated Excel output with enhanced logging

from flask import Blueprint, jsonify, send_file, current_app
import os

download_bp = Blueprint("download", __name__)

# ─── GET /api/download/<filename> ────────────────────────────
@download_bp.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    """
    Download the generated Excel output file.
    Example: GET /api/download/engineering_abc12345_output.xlsx
    """
    # Security: strip any path traversal
    filename    = os.path.basename(filename)
    output_dir  = current_app.config["OUTPUT_DIR"]
    file_path   = os.path.join(output_dir, filename)

    # ✅ ENHANCED LOGGING
    print(f"\n{'='*60}")
    print(f"[DOWNLOAD REQUEST]")
    print(f"Requested filename: {filename}")
    print(f"Full file path: {file_path}")
    print(f"Output directory: {output_dir}")
    print(f"Output dir exists: {os.path.exists(output_dir)}")
    
    if os.path.exists(output_dir):
        files_in_dir = os.listdir(output_dir)
        print(f"Files in output directory: {files_in_dir}")
    else:
        print(f"⚠️  Output directory does NOT exist!")
    
    file_exists = os.path.exists(file_path)
    print(f"Requested file exists: {file_exists}")
    print(f"{'='*60}\n")

    if not file_exists:
        # Enhanced error response
        error_response = {
            "success": False,
            "error": "File not found",
            "details": {
                "requested_file": filename,
                "output_dir": output_dir,
                "output_dir_exists": os.path.exists(output_dir)
            }
        }
        
        if os.path.exists(output_dir):
            error_response["details"]["files_available"] = os.listdir(output_dir)
        
        print(f"[ERROR] Download failed: {error_response}")
        return jsonify(error_response), 404

    try:
        # Verify file is readable
        if not os.access(file_path, os.R_OK):
            print(f"[ERROR] File exists but is not readable: {file_path}")
            return jsonify({"success": False, "error": "File not readable"}), 403

        file_size = os.path.getsize(file_path)
        print(f"[SUCCESS] Sending file: {filename} ({file_size} bytes)")
        
        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        print(f"[ERROR] Exception during download: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET /api/outputs — List all output files ────────────────
@download_bp.route("/outputs", methods=["GET"])
def list_outputs():
    """List all generated Excel output files with enhanced info."""
    output_dir = current_app.config["OUTPUT_DIR"]
    files = []
    
    print(f"\n[LIST OUTPUTS] Listing files in: {output_dir}")
    
    try:
        if not os.path.exists(output_dir):
            print(f"[WARN] Output dir doesn't exist: {output_dir}")
            return jsonify({
                "success": True,
                "files": [],
                "total": 0,
                "output_dir": output_dir,
                "output_dir_exists": False
            })
        
        dir_contents = os.listdir(output_dir)
        print(f"[LIST] Directory contents: {dir_contents}")
        
        for f in dir_contents:
            if f.endswith(".xlsx"):
                path = os.path.join(output_dir, f)
                try:
                    size_kb = round(os.path.getsize(path) / 1024, 1)
                    files.append({
                        "filename": f,
                        "size_kb": size_kb,
                        "url": f"/api/download/{f}",
                        "created": os.path.getctime(path),
                        "modified": os.path.getmtime(path)
                    })
                    print(f"  ✓ {f} ({size_kb} KB)")
                except Exception as e:
                    print(f"  ✗ Error reading {f}: {e}")
        
        files.sort(key=lambda x: x["filename"])
        result = {
            "success": True,
            "files": files,
            "total": len(files),
            "output_dir": output_dir,
            "output_dir_exists": True
        }
        print(f"[LIST] Total .xlsx files: {len(files)}\n")
        return jsonify(result)
        
    except Exception as e:
        print(f"[ERROR] Exception in list_outputs: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "output_dir": output_dir
        }), 500