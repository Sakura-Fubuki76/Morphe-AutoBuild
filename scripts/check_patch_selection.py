#!/usr/bin/env python3
"""Validate that the patch names used in scripts/build-morphe.sh still exist.

The Morphe CLI silently ignores unknown ``-d``/``--disable`` and
``-e``/``--enable`` patch names.  When upstream renames or removes a patch, the
build keeps "succeeding" while the customization no longer does anything (for
example ``Change package name`` was renamed to ``Clone app``).  This script
checks the configured names against the actual ``.mpp`` bundle before patching
and fails the build when a name is missing so the stale flag is noticed.

Exit codes: 0 = selection valid (possibly with no-op warnings), 2 = invalid.
"""
import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI_JAR = ROOT / "tools" / "morphe-cli.jar"

_NAME_LINE = re.compile(r"^Name: (.+)$", re.MULTILINE)
_ENABLED_LINE = re.compile(r"^Enabled: (true|false)$", re.MULTILINE)
_NO_PATCHES = "No compatible patches found"


def _strip_log_prefix(line):
    return line[len("INFO: ") :] if line.startswith("INFO: ") else line


def _run_list_patches(cli_jar, patches_mpp, package, include_experimental):
    cmd = [
        "java",
        "-jar",
        str(cli_jar),
        "list-patches",
        "--patches",
        str(patches_mpp),
        "-f",
        package,
    ]
    if include_experimental:
        cmd.append("-x")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(ROOT),
    )


def load_patches(cli_jar, patches_mpp, package):
    """Return ``{lowercase name: (name, enabled_by_default)}`` for *package*."""
    # The target versions resolved by find_target_version.py are experimental,
    # so experimental versions must be included here as well; otherwise every
    # patch for the app would be filtered out.
    proc = _run_list_patches(cli_jar, patches_mpp, package, True)
    if proc.returncode != 0:
        lowered = (proc.stderr + proc.stdout).lower()
        if "unknown option" in lowered or "unmatched argument" in lowered:
            proc = _run_list_patches(cli_jar, patches_mpp, package, False)
    output = "\n".join(
        _strip_log_prefix(line) for line in proc.stdout.splitlines()
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(
            f"list-patches failed for {package}: "
            f"{detail[-1] if detail else 'no output'}"
        )
    if _NO_PATCHES in output:
        raise RuntimeError(
            f"no patches in {patches_mpp} are compatible with {package}"
        )

    patches = {}
    for block in output.split("\n\n"):
        name_match = _NAME_LINE.search(block)
        if not name_match:
            continue
        enabled_match = _ENABLED_LINE.search(block)
        enabled = enabled_match.group(1) == "true" if enabled_match else None
        name = name_match.group(1).strip()
        patches[name.lower()] = (name, enabled)
    if not patches:
        raise RuntimeError(
            f"could not read any patch names from {patches_mpp} for {package}"
        )
    return patches


def suggest(name, known):
    matches = difflib.get_close_matches(name.lower(), known, n=3, cutoff=0.5)
    if not matches:
        return ""
    return " (did you mean: " + ", ".join(matches) + "?)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--patches-mpp", required=True)
    parser.add_argument("--cli-jar", default=str(CLI_JAR))
    parser.add_argument(
        "-d",
        "--disable",
        action="append",
        default=[],
        metavar="NAME",
        help="Patch name disabled by the build script",
    )
    parser.add_argument(
        "-e",
        "--enable",
        action="append",
        default=[],
        metavar="NAME",
        help="Patch name enabled by the build script",
    )
    args = parser.parse_args()

    try:
        patches = load_patches(args.cli_jar, args.patches_mpp, args.package)
    except Exception as exc:  # noqa: BLE001 - reported to the build log
        print(f"[check] ERROR: {exc}", file=sys.stderr)
        return 2

    errors = []
    warnings = []
    for name in args.disable:
        hit = patches.get(name.lower())
        if hit is None:
            errors.append(
                f"disabled patch {name!r} does not exist in "
                f"{Path(args.patches_mpp).name}{suggest(name, patches)}"
            )
        elif hit[1] is False:
            warnings.append(
                f"disabled patch {name!r} is already disabled by default (no-op)"
            )
    for name in args.enable:
        hit = patches.get(name.lower())
        if hit is None:
            errors.append(
                f"enabled patch {name!r} does not exist in "
                f"{Path(args.patches_mpp).name}{suggest(name, patches)}"
            )
        elif hit[1] is True:
            warnings.append(
                f"enabled patch {name!r} is already enabled by default (no-op)"
            )

    for warning in warnings:
        print(f"[check] WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"[check] ERROR: {error}", file=sys.stderr)
        return 2

    print(
        f"[check] {args.package}: {len(patches)} patch(es) checked, "
        f"{len(args.disable)} disable(s), {len(args.enable)} enable(s) valid",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())