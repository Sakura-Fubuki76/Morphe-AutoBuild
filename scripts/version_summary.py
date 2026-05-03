#!/usr/bin/env python3
import argparse
import json


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clean(value):
    value = str(value or "unknown")
    return "".join(c if c.isalnum() or c in ".-_" else "-" for c in value).strip("-")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--youtube")
    p.add_argument("--reddit")
    p.add_argument("--twitter")
    p.add_argument("--patches", required=True)
    p.add_argument("--piko-patches")
    args = p.parse_args()
    yt = load(args.youtube) if args.youtube else {}
    reddit = load(args.reddit) if args.reddit else {}
    twitter = load(args.twitter) if args.twitter else {}
    patches = load(args.patches)
    piko_patches = load(args.piko_patches) if args.piko_patches else {}
    parts = []
    if yt:
        parts.append(f"yt-{clean(yt.get('version') or yt.get('title') or 'youtube')}")
    if reddit:
        parts.append(f"reddit-{clean(reddit.get('version') or reddit.get('title') or 'reddit')}")
    if twitter:
        parts.append(f"twitter-{clean(twitter.get('version') or twitter.get('title') or 'twitter')}")
    if not parts:
        parts.append("no-apps")
    parts.append(clean(patches.get("tag_name") or "patches"))
    if piko_patches:
        parts.append(clean(piko_patches.get("tag_name") or "piko"))
    print("-".join(parts))


if __name__ == "__main__":
    main()
