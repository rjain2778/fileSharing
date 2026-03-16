import os
import io
import zipfile
import socket
import qrcode
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = Path(__file__).parent / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"txt", "pdf", "png", "jpg", "jpeg", "gif", "mp4", "mp3", "zip", "doc", "docx", "xls", "xlsx", "ppt", "pptx"}


def get_local_ip():
    """Get the local IP address of the machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def generate_qr_code(ip, port):
    """Generate a QR code for the given IP and port."""
    url = f"http://{ip}:{port}"
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def sanitize_path(path):
    """Sanitize and validate the path to prevent directory traversal."""
    if not path:
        return UPLOAD_FOLDER

    # Remove any .. to prevent directory traversal
    path = path.replace("..", "").replace("//", "/").strip("/")

    full_path = UPLOAD_FOLDER / path

    # Ensure the path is within UPLOAD_FOLDER
    try:
        full_path.relative_to(UPLOAD_FOLDER)
    except ValueError:
        return UPLOAD_FOLDER

    return full_path


def human_readable_size(size_bytes):
    """Convert bytes to human readable format."""
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0

    while size_bytes >= 1024 and unit_index < len(units) - 1:
        size_bytes /= 1024
        unit_index += 1

    return f"{size_bytes:.1f} {units[unit_index]}"


def get_unique_filename(folder, filename):
    """Generate a unique filename if file already exists."""
    base_path = folder / filename
    if not base_path.exists():
        return filename

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1

    while True:
        new_filename = f"{stem} ({counter}){suffix}"
        new_path = folder / new_filename
        if not new_path.exists():
            return new_filename
        counter += 1


@app.route("/")
def index():
    """Render the main page."""
    ip = get_local_ip()
    port = request.environ.get("SERVER_PORT", 5000)
    return render_template("index.html", ip=ip, port=port)


@app.route("/api/files")
def list_files():
    """List files and folders in the given path."""
    path = request.args.get("path", "")
    current_path = sanitize_path(path)

    if not current_path.exists():
        return jsonify({"error": "Path not found"}), 404

    items = []

    # Add parent directory if not at root
    if current_path != UPLOAD_FOLDER:
        parent = str(current_path.parent.relative_to(UPLOAD_FOLDER))
        if parent == ".":
            parent = ""
        items.append({
            "name": "..",
            "type": "parent",
            "path": parent,
            "size": "",
            "modified": ""
        })

    # List all items
    for item in sorted(current_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            stat = item.stat()
            items.append({
                "name": item.name,
                "type": "folder" if item.is_dir() else "file",
                "path": str(item.relative_to(UPLOAD_FOLDER)),
                "size": human_readable_size(stat.st_size) if item.is_file() else "",
                "modified": int(stat.st_mtime)
            })
        except (OSError, PermissionError):
            continue

    return jsonify({
        "items": items,
        "current_path": path
    })


@app.route("/api/folder", methods=["POST"])
def create_folder():
    """Create a new folder."""
    data = request.get_json()

    if not data or "name" not in data:
        return jsonify({"error": "Folder name required"}), 400

    folder_name = data["name"].strip()
    parent_path = data.get("path", "")

    # Validate folder name
    if not folder_name:
        return jsonify({"error": "Folder name cannot be empty"}), 400

    if "/" in folder_name or "\\" in folder_name:
        return jsonify({"error": "Folder name cannot contain path separators"}), 400

    # Sanitize the folder name
    folder_name = secure_filename(folder_name)
    if not folder_name:
        return jsonify({"error": "Invalid folder name"}), 400

    parent = sanitize_path(parent_path)
    new_folder = parent / folder_name

    try:
        new_folder.mkdir(exist_ok=False)
        return jsonify({"message": "Folder created successfully", "path": str(new_folder.relative_to(UPLOAD_FOLDER))})
    except FileExistsError:
        return jsonify({"error": "Folder already exists"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload", methods=["POST"])
def upload_files():
    """Upload files to the server."""
    if "files" not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist("files")
    path = request.form.get("path", "")
    target_folder = sanitize_path(path)

    if not target_folder.exists():
        return jsonify({"error": "Target folder not found"}), 404

    uploaded = []
    errors = []

    for file in files:
        if file.filename == "":
            continue

        filename = secure_filename(file.filename)
        if not filename:
            errors.append(f"Invalid filename: {file.filename}")
            continue

        # Get unique filename
        unique_name = get_unique_filename(target_folder, filename)
        file_path = target_folder / unique_name

        try:
            file.save(str(file_path))
            uploaded.append(unique_name)
        except Exception as e:
            errors.append(f"Failed to save {filename}: {str(e)}")

    return jsonify({
        "uploaded": uploaded,
        "errors": errors,
        "message": f"Uploaded {len(uploaded)} file(s)"
    })


@app.route("/api/download")
def download_file():
    """Download a single file."""
    path = request.args.get("path", "")
    file_path = sanitize_path(path)

    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404

    if file_path.is_dir():
        return jsonify({"error": "Cannot download a folder directly"}), 400

    return send_file(file_path, as_attachment=True)


@app.route("/api/download-zip", methods=["POST"])
def download_zip():
    """Download multiple files as a ZIP archive."""
    data = request.get_json()

    if not data or "paths" not in data or not data["paths"]:
        return jsonify({"error": "No files selected"}), 400

    paths = data["paths"]

    # Create ZIP in memory
    memory_file = io.BytesIO()

    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            file_path = sanitize_path(path)

            if not file_path.exists():
                continue

            if file_path.is_file():
                arcname = file_path.name
                zf.write(file_path, arcname)
            elif file_path.is_dir():
                # Add entire folder recursively
                for item in file_path.rglob("*"):
                    if item.is_file():
                        arcname = str(item.relative_to(file_path.parent))
                        zf.write(item, arcname)

    memory_file.seek(0)

    return send_file(
        memory_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name="download.zip"
    )


@app.route("/api/qr")
def get_qr_code():
    """Generate QR code for the app URL."""
    ip = get_local_ip()
    port = request.environ.get("SERVER_PORT", 5000)
    qr_buffer = generate_qr_code(ip, port)
    return send_file(qr_buffer, mimetype="image/png")


if __name__ == "__main__":
    print(f"=" * 50)
    print(f"File Transfer App")
    print(f"=" * 50)
    print(f"Local IP: {get_local_ip()}")
    print(f"Port: 5000")
    print(f"URL: http://{get_local_ip()}:5000")
    print(f"=" * 50)
    print(f"Upload folder: {UPLOAD_FOLDER}")
    print(f"=" * 50)

    app.run(host="0.0.0.0", port=5000, debug=True)
