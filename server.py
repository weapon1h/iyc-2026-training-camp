from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import shutil
import subprocess
import threading
import zipfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
ASSET_ROOT = ROOT / "IYC-2026 资料汇总"
ARCHIVE_NAME = "IYC-2026-complete-originals.zip"
ARCHIVE_PATH = ROOT / ARCHIVE_NAME
PREVIEW_ROOT = ROOT / ".preview-cache"
ARCHIVE_LOCK = threading.Lock()
PREVIEW_LOCK = threading.Lock()

CATEGORY_LABELS = {
    "photos": "Photo originals",
    "course": "Course material",
    "documents": "Other files",
    "videos": "Video originals",
    "other": "Other",
}

PPT_PREVIEW_PDFS = {
    "2026-11-02_Black_Holes_Feng_Yuan_source.pptx": "2026-11-02_袁峰_黑洞_Black_Hole.pdf",
    "2026-11-03_The_AI_Era_AI4Fusion_source.pptx": "2026-11-03_AI时代与AI4Fusion_东昇聚变团队.pdf",
}


def category_for(path: Path) -> str:
    parts = path.parts
    for folder, category in (("01_照片", "photos"), ("02_课程资料", "course"), ("03_文档", "documents"), ("04_视频", "videos")):
        if folder in parts:
            return category
    return "other"


def file_records() -> list[dict]:
    records = []
    if not ASSET_ROOT.exists():
        return records
    for path in sorted(ASSET_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        category = category_for(path)
        records.append({
            "name": path.name,
            "path": relative,
            "category": category,
            "categoryLabel": CATEGORY_LABELS[category],
            "bytes": path.stat().st_size,
            "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
    return records


def archive_is_current() -> bool:
    if not ARCHIVE_PATH.exists():
        return False
    archive_mtime = ARCHIVE_PATH.stat().st_mtime
    return all(path.stat().st_mtime <= archive_mtime for path in ASSET_ROOT.rglob("*") if path.is_file())


def asset_path(requested: str) -> Path | None:
    candidate = (ROOT / requested).resolve()
    if not candidate.is_file() or ASSET_ROOT not in candidate.parents:
        return None
    return candidate


def preview_source(path: Path) -> Path | None:
    if path.suffix.lower() == ".pptx":
        paired = path.with_name(PPT_PREVIEW_PDFS.get(path.name, path.stem + ".pdf"))
        return paired if paired.is_file() else None
    if path.suffix.lower() == ".pdf":
        return path
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".mp4", ".mov", ".m4v"}:
        return path
    return None


def preview_file(path: Path) -> Path | None:
    source = preview_source(path)
    if source is None:
        return None
    cache_key = hashlib.sha1(f"{source.relative_to(ROOT)}:{source.stat().st_mtime_ns}:{source.stat().st_size}".encode()).hexdigest()
    target = PREVIEW_ROOT / f"{cache_key}.jpg"
    if target.exists():
        return target
    with PREVIEW_LOCK:
        if target.exists():
            return target
        PREVIEW_ROOT.mkdir(exist_ok=True)
        try:
            if source.suffix.lower() == ".pdf":
                import fitz

                with fitz.open(source) as document:
                    if not document.page_count:
                        return None
                    page = document[0]
                    scale = min(1.2, 900 / max(page.rect.width, page.rect.height))
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    pixmap.save(target)
            elif source.suffix.lower() in {".heic", ".heif"}:
                powershell_script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$decoder = [System.Windows.Media.Imaging.BitmapDecoder]::Create([Uri]$env:IYC_PREVIEW_SOURCE, [System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat, [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad)
$frame = $decoder.Frames[0]
$scale = [Math]::Min(1.0, [Math]::Min(900.0 / $frame.PixelWidth, 650.0 / $frame.PixelHeight))
$bitmap = [System.Windows.Media.Imaging.TransformedBitmap]::new($frame, [System.Windows.Media.ScaleTransform]::new($scale, $scale))
$encoder = [System.Windows.Media.Imaging.JpegBitmapEncoder]::new()
$encoder.QualityLevel = 78
$encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
$stream = [System.IO.File]::Open($env:IYC_PREVIEW_TARGET, [System.IO.FileMode]::Create)
try { $encoder.Save($stream) } finally { $stream.Dispose() }
"""
                preview_environment = os.environ.copy()
                preview_environment["IYC_PREVIEW_SOURCE"] = str(source)
                preview_environment["IYC_PREVIEW_TARGET"] = str(target)
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell_script],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=preview_environment,
                )
            elif source.suffix.lower() in {".mp4", ".mov", ".m4v"}:
                import cv2
                from PIL import Image

                capture = cv2.VideoCapture(str(source))
                try:
                    frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
                    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_count * 0.08)))
                    success, frame = capture.read()
                    if not success:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        success, frame = capture.read()
                    if not success:
                        return None
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(frame)
                    image.thumbnail((1200, 800), Image.Resampling.LANCZOS)
                    image.save(target, "JPEG", quality=82, optimize=True)
                finally:
                    capture.release()
            else:
                from PIL import Image, ImageOps

                with Image.open(source) as image:
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    image.thumbnail((900, 650), Image.Resampling.LANCZOS)
                    image.save(target, "JPEG", quality=78, optimize=True)
        except Exception as error:
            print(f"Preview unavailable for {source.name}: {error}", flush=True)
            return None
    return target


def build_archive() -> None:
    with ARCHIVE_LOCK:
        if archive_is_current():
            return
        temporary = ARCHIVE_PATH.with_suffix(".zip.part")
        if temporary.exists():
            temporary.unlink()
        print("Building full archive. This can take a few minutes for the 7 GB source folder…", flush=True)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path in sorted(ASSET_ROOT.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(ROOT).as_posix())
        temporary.replace(ARCHIVE_PATH)
        print(f"Ready: {ARCHIVE_PATH.name}", flush=True)


class ArchiveHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, payload: object, status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/manifest":
            records = file_records()
            self.send_json({"files": records, "count": len(records)})
            return
        if parsed.path == "/api/preview":
            requested = parse_qs(parsed.query).get("path", [""])[0]
            source = asset_path(requested)
            if source is None:
                self.send_error(404, "File not found")
                return
            preview = preview_file(source)
            if preview is None:
                self.send_error(404, "Preview unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(preview.stat().st_size))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            with preview.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)
            return
        if parsed.path == "/download/all":
            try:
                build_archive()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{ARCHIVE_NAME}"')
                self.send_header("Content-Length", str(ARCHIVE_PATH.stat().st_size))
                self.end_headers()
                with ARCHIVE_PATH.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
            except Exception as error:
                self.send_error(500, f"Unable to create archive: {error}")
            return
        if parsed.path == "/download/file":
            requested = parse_qs(parsed.query).get("path", [""])[0]
            candidate = asset_path(requested)
            if candidate is None:
                self.send_error(404, "File not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{candidate.name}"')
            self.send_header("Content-Length", str(candidate.stat().st_size))
            self.end_headers()
            with candidate.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
            return
        super().do_GET()


def main() -> None:
    host = os.environ.get("IYC_HOST", "127.0.0.1")
    port = int(os.environ.get("IYC_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), ArchiveHandler)
    print(f"IYC 2026 archive ready at http://{host}:{port}/", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
