# -*- coding: utf-8 -*-
"""截图助手：adb 抓屏 → 缩到 720 宽 JPEG（<500KB），给多模态读图用。

用法：
    python work/shot.py out.jpg [设备]
"""
import subprocess
import sys

from PIL import Image

ADB = "D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
DEV = "emulator-5554"


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "logs/shots/cur.jpg"
    dev = sys.argv[2] if len(sys.argv) > 2 else DEV
    png = out.rsplit(".", 1)[0] + "_raw.png"
    with open(png, "wb") as f:
        subprocess.run([ADB, "-s", dev, "exec-out", "screencap", "-p"],
                       stdout=f, check=True)
    im = Image.open(png).convert("RGB")
    w, h = im.size
    nw = 720
    im = im.resize((nw, int(h * nw / w)), Image.LANCZOS)
    im.save(out, "JPEG", quality=78, optimize=True)
    print("%s  %dx%d  %d bytes  (原图 %dx%d)" %
          (out, im.size[0], im.size[1],
           __import__("os").path.getsize(out), w, h))


if __name__ == "__main__":
    main()
