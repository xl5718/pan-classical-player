"""生成应用图标：米色底上一张黑胶唱片，深红色标签。需要 macOS 自带的 iconutil 才能产出 .icns。"""

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "build"


def draw(size: int = 1024) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(s * 0.09)
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=int(s * 0.18), fill=(246, 243, 236, 255),
                        outline=(221, 214, 201, 255), width=max(1, s // 256))
    c, r = s / 2, s * 0.33
    d.ellipse([c - r, c - r, c + r, c + r], fill=(29, 27, 24, 255))
    for k in range(6):
        rr = r * (0.92 - k * 0.075)
        d.ellipse([c - rr, c - rr, c + rr, c + rr], outline=(58, 54, 49, 255), width=max(1, s // 400))
    lr = r * 0.36
    d.ellipse([c - lr, c - lr, c + lr, c + lr], fill=(139, 46, 34, 255))
    hr = r * 0.05
    d.ellipse([c - hr, c - hr, c + hr, c + hr], fill=(246, 243, 236, 255))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = draw()
    base.save(OUT / "icon.png")
    if not shutil.which("iconutil"):
        print("未找到 iconutil（不是 macOS），只生成了 icon.png")
        return
    iconset = OUT / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for px in (16, 32, 128, 256, 512):
        base.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{px}x{px}.png")
        base.resize((px * 2, px * 2), Image.LANCZOS).save(iconset / f"icon_{px}x{px}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "icon.icns")], check=True)
    print("已生成", OUT / "icon.icns")


if __name__ == "__main__":
    main()
