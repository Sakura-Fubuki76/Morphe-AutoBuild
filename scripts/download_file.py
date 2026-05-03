#!/usr/bin/env python3
import argparse
import json
import os
import urllib.request


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--meta", required=True)
    args = p.parse_args()

    req = urllib.request.Request(args.url, headers={"User-Agent": "Mozilla/5.0"})
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with urllib.request.urlopen(req, timeout=180) as res, open(args.out, "wb") as f:
        while True:
            chunk = res.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    with open(args.meta, "w", encoding="utf-8") as f:
        json.dump({"source": "manual-url", "url": args.url}, f, indent=2)


if __name__ == "__main__":
    main()
