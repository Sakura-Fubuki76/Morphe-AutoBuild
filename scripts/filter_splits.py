#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import zipfile

DROP_ARCH_MARKERS = ("armeabi", "armeabi_v7a", "armeabi-v7a", "x86", "x86_64", "x86-64")
_DROP_DENSITY_MARKERS = ("xxxhdpi", "xhdpi", "hdpi", "mdpi", "ldpi", "tvdpi")

_DROP_LANG_RE = re.compile(
    r"[.\-_](?:"
    r"af|am|ar|as|az|be|bg|bn|bs|ca|cs|da|de|el|en|es|et|eu|fa|fi|fr|gl|gu|"
    r"hi|hr|hu|hy|id|in|is|it|iw|ja|ka|kk|km|kn|ko|ky|lo|lt|lv|mk|ml|mn|mr|ms|"
    r"my|nb|ne|nl|or|pa|pl|pt|ro|ru|si|sk|sl|sq|sr|sv|sw|ta|te|th|tl|tr|uk|ur|"
    r"uz|vi"
    r")(?:[.\-_]|$)",
    re.IGNORECASE,
)


def should_keep(name):
    """Decide whether a split APK *file* should be kept."""
    lower = name.lower()
    if not lower.endswith(".apk"):
        return False

    basename = os.path.basename(lower)

    if "base" in basename or "master" in basename:
        return True

    if any(marker in lower for marker in DROP_ARCH_MARKERS) and "arm64" not in lower:
        return False

    for marker in _DROP_DENSITY_MARKERS:
        if f".{marker}" in lower or f"_{marker}" in lower or f"-{marker}" in lower:
            return False

    if _DROP_LANG_RE.search(lower):
        return False

    return True


def _extract_container(container, out_dir):
    """Extract matching APK files from a container (APKM/APKS/XAPK)."""
    count = 0
    with zipfile.ZipFile(container) as zf:
        for info in zf.infolist():
            if info.is_dir() or not should_keep(info.filename):
                continue
            target = os.path.join(out_dir, os.path.basename(info.filename))
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out-dir", required=True)
    args = p.parse_args()

    shutil.rmtree(args.out_dir, ignore_errors=True)
    os.makedirs(args.out_dir, exist_ok=True)

    if os.path.isdir(args.input):
        kept = 0
        for name in sorted(os.listdir(args.input)):
            path = os.path.join(args.input, name)
            if not os.path.isfile(path):
                continue
            lower = name.lower()
            if lower.endswith(".apk") and should_keep(name):
                shutil.copy2(path, os.path.join(args.out_dir, os.path.basename(name)))
                kept += 1
            elif lower.endswith((".apkm", ".apks", ".xapk")):
                kept += _extract_container(path, args.out_dir)
        if kept == 0:
            raise RuntimeError(f"No matching APK files found in: {args.input}")
    else:
        ext = os.path.splitext(args.input.lower())[1]
        if ext == ".apk":
            shutil.copy2(args.input, os.path.join(args.out_dir, "base.apk"))
        elif zipfile.is_zipfile(args.input):
            kept = _extract_container(args.input, args.out_dir)
            if kept == 0:
                raise RuntimeError(f"No matching APK files inside: {args.input}")
        else:
            shutil.copy2(args.input, os.path.join(args.out_dir, "base.apk"))


if __name__ == "__main__":
    main()
