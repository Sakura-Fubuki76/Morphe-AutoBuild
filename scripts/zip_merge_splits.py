#!/usr/bin/env python3
import argparse
import sys
import zipfile


SKIP_PREFIXES = ("META-INF/",)
SKIP_NAMES = ("AndroidManifest.xml",)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, help="Base APK to extend")
    p.add_argument("--out", required=True, help="Output merged APK")
    p.add_argument("splits", nargs="*", help="Config-split APKs to merge in")
    args = p.parse_args()

    if not args.splits:
        import shutil
        shutil.copy2(args.base, args.out)
        return

    entries = {}
    for apk in [args.base] + args.splits:
        with zipfile.ZipFile(apk) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if any(name.startswith(p) for p in SKIP_PREFIXES):
                    continue
                if name in SKIP_NAMES:
                    continue
                if name not in entries:
                    entries[name] = (z.read(info.filename), info.compress_type)

    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            data, ctype = entries[name]
            z.writestr(zipfile.ZipInfo(name), data, ctype)


if __name__ == "__main__":
    main()
