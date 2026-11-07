#!/usr/bin/env python3
"""Remove stale assets from the release that is about to be updated.

The workflow reuses the Morphe patches tag as the GitHub release tag, so every
build updates the same release.  ``softprops/action-gh-release`` overwrites
assets with the same name but never removes assets that are no longer produced
(for example a differently named X APK from an older build), which left
duplicate/outdated downloads on the release.  This script deletes assets that
match the build's artifact patterns but are not part of the current build.

Requires the ``gh`` CLI and ``GH_TOKEN`` in the environment (the workflow
provides both).
"""
import argparse
import fnmatch
import json
import os
import subprocess
import sys

DEFAULT_PATTERNS = ["YouTube-*", "Reddit-*", "X-*"]


def gh(*argv):
    return subprocess.run(
        ["gh", "api", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--tag", required=True, help="Release tag being updated")
    parser.add_argument(
        "--keep-file",
        required=True,
        help="File with one asset filename per line that must be kept",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        default=[],
        metavar="GLOB",
        help=f"Artifact filename patterns (default: {' '.join(DEFAULT_PATTERNS)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be deleted",
    )
    args = parser.parse_args()

    patterns = args.pattern
    keep = set()
    keep_path = args.keep_file
    try:
        with open(keep_path, "r", encoding="utf-8") as handle:
            keep = {line.strip() for line in handle if line.strip()}
    except OSError:
        print(
            f"[cleanup] WARNING: {keep_path} missing; "
            "skipping cleanup to avoid deleting everything",
            file=sys.stderr,
        )
        return 0
    # release-files.txt holds paths under dist/; the release asset names are
    # only the basenames.
    keep = {os.path.basename(name) for name in keep}

    if not keep:
        print(
            "[cleanup] WARNING: no artifacts in this build; "
            "skipping cleanup to keep the previous release assets",
            file=sys.stderr,
        )
        return 0

    if not patterns:
        # Only clean the artifact families that this build actually produced:
        # when an app fails, its old assets stay on the release instead of
        # disappearing until a later successful build replaces them.
        families = sorted(
            {name.split("-", 1)[0] for name in keep if "-" in name}
        )
        patterns = [f"{family}-*" for family in families]
        print(f"[cleanup] artifact patterns from this build: {', '.join(patterns)}")

    response = gh(f"repos/{args.repo}/releases/tags/{args.tag}")
    if response.returncode != 0:
        combined = (response.stderr + response.stdout).lower()
        if "not found" in combined or "http 404" in combined:
            print(f"[cleanup] release {args.tag} does not exist yet; nothing to clean")
            return 0
        print(
            f"[cleanup] ERROR: cannot read release {args.tag}: "
            f"{response.stderr.strip() or response.stdout.strip()}",
            file=sys.stderr,
        )
        return 1

    try:
        release = json.loads(response.stdout)
    except ValueError as exc:
        print(f"[cleanup] ERROR: invalid release JSON: {exc}", file=sys.stderr)
        return 1

    assets = release.get("assets") or []
    stale = [
        asset
        for asset in assets
        if asset.get("name") not in keep
        and any(fnmatch.fnmatch(asset.get("name", ""), pattern) for pattern in patterns)
    ]
    if not stale:
        print(f"[cleanup] release {args.tag}: no stale assets")
        return 0

    for asset in stale:
        name = asset.get("name", "?")
        if args.dry_run:
            print(f"[cleanup] would delete {name}")
            continue
        deleted = gh(
            "-X", "DELETE", f"repos/{args.repo}/releases/assets/{asset['id']}"
        )
        if deleted.returncode != 0:
            print(
                f"[cleanup] ERROR: failed to delete {name}: "
                f"{deleted.stderr.strip() or deleted.stdout.strip()}",
                file=sys.stderr,
            )
            return 1
        print(f"[cleanup] deleted {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())