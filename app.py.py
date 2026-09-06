import os
import json
import time
import uuid
import threading
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory, render_template, abort
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
META_FILE = os.path.join(BASE_DIR, "metadata.json")

DAYS_TO_LIVE = 20
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".txt", ".zip"}
CLEANUP_INTERVAL_SECONDS = 60 * 60  # check once an hour

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_SIZE_BYTES + (1 * 1024 * 1024)  # small buffer for form overhead
_meta_lock = threading.Lock()


# ---------- metadata helpers ----------

def _load_meta():
    if not os.path.exists(META_FILE):
        return {}
    try:
        with open(META_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_meta(meta):
    tmp_path = META_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp_path, META_FILE)


def _now():
    return time.time()


def _is_expired(uploaded_at):
    age_days = (_now() - uploaded_at) / 86400
    return age_days >= DAYS_TO_LIVE


def _extension(filename):
    return os.path.splitext(filename)[1].lower()


def cleanup_expired():
    """Remove any file whose age has passed DAYS_TO_LIVE."""
    with _meta_lock:
        meta = _load_meta()
        changed = False
        for file_id in list(meta.keys()):
            entry = meta[file_id]
            if _is_expired(entry["uploaded_at"]):
                stored_path = os.path.join(UPLOAD_DIR, entry["stored_name"])
                if os.path.exists(stored_path):
                    try:
                        os.remove(stored_path)
                    except OSError:
                        pass
                del meta[file_id]
                changed = True
        if changed:
            _save_meta(meta)


def _cleanup_loop():
    while True:
        cleanup_expired()
        time.sleep(CLEANUP_INTERVAL_SECONDS)


def start_background_cleanup():
    thread = threading.Thread(target=_cleanup_loop, daemon=True)
    thread.start()


# ---------- routes ----------

@app.route("/")
def index():
    return render_template(
        "index.html",
        days_to_live=DAYS_TO_LIVE,
        max_size_mb=MAX_SIZE_BYTES // (1024 * 1024),
    )


@app.route("/api/files", methods=["GET"])
def list_files():
    cleanup_expired()
    with _meta_lock:
        meta = _load_meta()

    files = []
    for file_id, entry in meta.items():
        elapsed_days = (_now() - entry["uploaded_at"]) / 86400
        days_left = max(0, round(DAYS_TO_LIVE - elapsed_days + 0.5))
        files.append({
            "id": file_id,
            "name": entry["original_name"],
            "size": entry["size"],
            "uploaded_at": entry["uploaded_at"],
            "uploaded_iso": datetime.fromtimestamp(entry["uploaded_at"], tz=timezone.utc).isoformat(),
            "days_left": days_left,
        })

    files.sort(key=lambda f: f["uploaded_at"], reverse=True)
    return jsonify(files=files)


@app.route("/api/upload", methods=["POST"])
def upload_file():
    cleanup_expired()

    if "file" not in request.files:
        return jsonify(error="No file part in the request."), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify(error="No file selected."), 400

    original_name = secure_filename(f.filename)
    ext = _extension(original_name)
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(error="Only .txt and .zip files are allowed."), 400

    contents = f.read()
    if len(contents) > MAX_SIZE_BYTES:
        return jsonify(error="File too large. Max size is 10 MB."), 400

    if ext == ".txt":
        try:
            contents.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify(error="Text file does not look like plain text (UTF-8)."), 400
    elif ext == ".zip":
        # Minimal signature check: ZIP files start with "PK"
        if not contents.startswith(b"PK"):
            return jsonify(error="File does not look like a valid ZIP archive."), 400

    file_id = uuid.uuid4().hex
    stored_name = f"{file_id}{ext}"
    stored_path = os.path.join(UPLOAD_DIR, stored_name)

    with open(stored_path, "wb") as out:
        out.write(contents)

    with _meta_lock:
        meta = _load_meta()
        meta[file_id] = {
            "original_name": original_name,
            "stored_name": stored_name,
            "size": len(contents),
            "uploaded_at": _now(),
        }
        _save_meta(meta)

    return jsonify(id=file_id, name=original_name, size=len(contents)), 201


@app.route("/api/download/<file_id>", methods=["GET"])
def download_file(file_id):
    cleanup_expired()
    with _meta_lock:
        meta = _load_meta()
        entry = meta.get(file_id)

    if not entry:
        abort(404, description="File not found or has expired.")

    ext = _extension(entry["stored_name"])
    mimetype = "application/zip" if ext == ".zip" else "text/plain"

    return send_from_directory(
        UPLOAD_DIR,
        entry["stored_name"],
        as_attachment=True,
        download_name=entry["original_name"],
        mimetype=mimetype,
    )


@app.route("/api/files/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    with _meta_lock:
        meta = _load_meta()
        entry = meta.pop(file_id, None)
        if entry:
            stored_path = os.path.join(UPLOAD_DIR, entry["stored_name"])
            if os.path.exists(stored_path):
                try:
                    os.remove(stored_path)
                except OSError:
                    pass
            _save_meta(meta)

    if not entry:
        return jsonify(error="File not found."), 404
    return jsonify(deleted=True)


@app.errorhandler(413)
def too_large(e):
    return jsonify(error="File too large. Max size is 10 MB."), 413


if __name__ == "__main__":
    start_background_cleanup()
    app.run(host="0.0.0.0", port=5000, debug=True)
