"""Image and video processing.

Everything happens at upload time so the public site only ever serves
pre-sized, pre-compressed files from the CDN.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 60_000_000

LABELS = ("thumb", "card", "full")
MAX_SOURCE_EDGE = 2400


def _to_rgb(im: Image.Image) -> Image.Image:
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return im.convert("RGB")


def lqip_data_uri(im: Image.Image) -> str:
    tiny = im.copy()
    tiny.thumbnail((24, 24))
    buf = BytesIO()
    tiny.save(buf, "WEBP", quality=40, method=0)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def build_image_variants(
    data: bytes, widths: tuple[int, ...], quality: int = 78
) -> tuple[dict, list[dict]]:
    """Return (metadata, variants).

    Each variant dict has label/format/width/height/data.
    """
    widths = tuple(widths) or (400, 800, 1600)
    while len(widths) < 3:
        widths = widths + (widths[-1],)

    im = _to_rgb(Image.open(BytesIO(data)))
    if max(im.size) > MAX_SOURCE_EDGE:
        im.thumbnail((MAX_SOURCE_EDGE, MAX_SOURCE_EDGE), Image.LANCZOS)

    meta = {
        "width": im.width,
        "height": im.height,
        "checksum": hashlib.sha256(data).hexdigest(),
        "lqip": lqip_data_uri(im),
    }

    variants: list[dict] = []
    for label, target_w in zip(LABELS, widths[:3]):
        if im.width > target_w:
            height = max(1, round(im.height * target_w / im.width))
            resized = im.resize((target_w, height), Image.LANCZOS)
        else:
            resized = im.copy()
        buf = BytesIO()
        resized.save(buf, "WEBP", quality=quality, method=4)
        variants.append(
            {
                "label": label,
                "format": "webp",
                "width": resized.width,
                "height": resized.height,
                "data": buf.getvalue(),
            }
        )

    # A clean, metadata-free original for customers who ask for the file.
    buf = BytesIO()
    im.save(buf, "JPEG", quality=88, optimize=True, progressive=True)
    variants.append(
        {
            "label": "original",
            "format": "jpg",
            "width": im.width,
            "height": im.height,
            "data": buf.getvalue(),
        }
    )
    return meta, variants


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


class VideoToolingMissing(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    info = json.loads(out.stdout or b"{}")
    video = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    return {
        "duration_s": float(info.get("format", {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
    }


def transcode_video(data: bytes, quality: int = 78) -> tuple[dict, list[dict]]:
    """Transcode to a web-safe H.264 MP4 (+ poster frame).

    Raises VideoToolingMissing when ffmpeg is not installed locally.
    """
    if not ffmpeg_available():
        raise VideoToolingMissing(
            "ffmpeg/ffprobe are not installed. Install ffmpeg, or upload the "
            "already-compressed .mp4 file instead."
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        source = tmpdir / "source"
        source.write_bytes(data)
        info = _probe(source)

        mp4_path = tmpdir / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(source),
                "-vf", "scale='min(1280,iw)':-2",
                "-c:v", "libx264", "-preset", "slow", "-crf", "24",
                "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-ac", "2",
                "-movflags", "+faststart",
                str(mp4_path),
            ],
            capture_output=True,
            check=True,
        )
        poster_path = tmpdir / "poster.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "0.5", "-i", str(mp4_path),
                "-frames:v", "1", "-vf", "scale=800:-2", str(poster_path),
            ],
            capture_output=True,
            check=True,
        )

        mp4 = mp4_path.read_bytes()
        poster_im = _to_rgb(Image.open(poster_path))
        meta = {
            "duration_s": info["duration_s"],
            "width": info["width"],
            "height": info["height"],
            "checksum": hashlib.sha256(data).hexdigest(),
            "lqip": lqip_data_uri(poster_im),
        }
        poster_buf = BytesIO()
        poster_im.save(poster_buf, "WEBP", quality=quality, method=4)
        variants = [
            {
                "label": "video",
                "format": "mp4",
                "width": info["width"],
                "height": info["height"],
                "data": mp4,
            },
            {
                "label": "poster",
                "format": "webp",
                "width": poster_im.width,
                "height": poster_im.height,
                "data": poster_buf.getvalue(),
            },
        ]
        return meta, variants
