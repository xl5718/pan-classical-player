import io
from pathlib import Path

from PIL import Image, ImageOps

THUMB = 600


def save_cover(data: bytes, covers_dir: Path, album_id: int) -> str:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    orig_dir = covers_dir / "orig"
    orig_dir.mkdir(exist_ok=True)
    ext = {"JPEG": "jpg", "PNG": "png"}.get(img.format or "", "img")
    for old in orig_dir.glob(f"{album_id}.*"):
        old.unlink()
    (orig_dir / f"{album_id}.{ext}").write_bytes(data)
    img = img.convert("RGB")
    img.thumbnail((THUMB, THUMB), Image.LANCZOS)
    name = f"{album_id}.jpg"
    img.save(covers_dir / name, "JPEG", quality=86, optimize=True, progressive=True)
    return name
