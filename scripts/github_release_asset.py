#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.request


def request_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "morphe-ksu-builder",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res)


def download(url, out):
    headers = {"User-Agent": "morphe-ksu-builder"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as res, open(out, "wb") as f:
        while True:
            chunk = res.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def select_release(releases, mode):
    candidates = [r for r in releases if not r.get("draft")]
    if mode == "latest-prerelease":
        candidates = [r for r in candidates if r.get("prerelease")]
    elif mode == "latest-stable":
        candidates = [r for r in candidates if not r.get("prerelease")]
    elif mode != "latest-any":
        raise ValueError(f"Unknown mode: {mode}")
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
    return candidates[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--mode", required=True, choices=["latest-prerelease", "latest-stable", "latest-any"])
    p.add_argument("--asset-regex", default=".*")
    p.add_argument("--out")
    p.add_argument("--meta", required=True)
    p.add_argument("--tag-only", action="store_true")
    args = p.parse_args()

    releases = request_json(f"https://api.github.com/repos/{args.repo}/releases?per_page=100")
    release = select_release(releases, args.mode)
    if not release:
        print(f"No release matched {args.mode} for {args.repo}", file=sys.stderr)
        return 2

    meta = {
        "repo": args.repo,
        "mode": args.mode,
        "tag_name": release.get("tag_name"),
        "name": release.get("name"),
        "published_at": release.get("published_at"),
        "html_url": release.get("html_url"),
        "prerelease": release.get("prerelease"),
    }
    os.makedirs(os.path.dirname(args.meta), exist_ok=True)
    with open(args.meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    if args.tag_only:
        return 0

    regex = re.compile(args.asset_regex, re.I)
    assets = release.get("assets") or []
    asset = next((a for a in assets if regex.search(a.get("name", ""))), None)
    if not asset:
        print(f"No asset matching {args.asset_regex} in {args.repo} {release.get('tag_name')}", file=sys.stderr)
        return 3

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    download(asset["browser_download_url"], args.out)
    meta["asset"] = {"name": asset.get("name"), "url": asset.get("browser_download_url")}
    with open(args.meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
