#!/usr/bin/env python3
"""Resolve the newest app version supported by a Morphe patch bundle.

Uses the CLI's ``list-versions`` command instead of loading the patch bundle
through the patcher API.  Older versions of this script compiled a Java helper
against ``app.morphe.patcher`` internals, which broke whenever the Morphe CLI
changed its Java API (for example CLI 2.1.0-dev.4 removed the ``Patch<T>``
type parameter and changed ``loadPatchesFromJar``'s return type).  Relying on
the CLI command keeps this script stable across CLI releases.

Selection rule: among the versions reported by ``list-versions``, prefer the
ones supported by the most (default-enabled) patches, then take the newest of
those.  This avoids picking a freshly-published version that only one patch
knows about, which would make most patches fail after ``--force``.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI_JAR = ROOT / "tools" / "morphe-cli.jar"
PATCHES_MPP = ROOT / "tools" / "patches.mpp"
BUILD_DIR = ROOT / "build"

# Example lines produced by `list-versions` (Morphe CLI 2.x):
#   INFO: Package name: com.google.android.youtube
#   Most common compatible versions:
#           21.44.528 (112 patches)
#           21.44.528 [versionCodes: ARM64_V8A=...] (112 patches)
_PACKAGE_LINE = re.compile(r"Package name:\s*(?P<pkg>\S+)")
_VERSION_LINE = re.compile(
    r"^\s*(?P<version>\S+)(?:\s+\[versionCodes:[^\]]*\])?\s+"
    r"\((?P<count>\d+)\s+patch(?:es)?\)\s*$"
)


def _run_list_versions(cli_jar, patches_mpp, package, include_experimental):
    cmd = [
        "java",
        "-jar",
        str(cli_jar),
        "list-versions",
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
        timeout=180,
        cwd=str(ROOT),
    )


def parse_versions(output, package):
    """Return ``[(version, patch_count)]`` for *package* from the CLI output."""
    entries = []
    in_section = False
    for line in output.splitlines():
        package_match = _PACKAGE_LINE.search(line)
        if package_match:
            in_section = package_match.group("pkg") == package
            continue
        if not in_section:
            continue
        version_match = _VERSION_LINE.match(line)
        if version_match:
            entries.append(
                (version_match.group("version"), int(version_match.group("count")))
            )
    return entries


def version_key(version):
    """Natural sort key for app versions such as ``12.19.1-release.0``."""
    key = []
    for part in re.split(r"[-._]", version):
        try:
            key.append((0, int(part)))
        except ValueError:
            key.append((1, part))
    return key


def choose_target(entries, no_beta):
    candidates = entries
    if no_beta:
        candidates = [e for e in candidates if "beta" not in e[0].lower()]
    if not candidates:
        return None
    best_count = max(count for _, count in candidates)
    best = [version for version, count in candidates if count == best_count]
    return max(best, key=version_key)


def resolve_target(cli_jar, patches_mpp, package, include_experimental, no_beta):
    proc = _run_list_versions(cli_jar, patches_mpp, package, include_experimental)
    if proc.returncode != 0 and include_experimental:
        # Older CLI releases may not know about -x; retry without experimental
        # versions rather than failing the whole build.
        lowered = (proc.stderr + proc.stdout).lower()
        if "unknown option" in lowered or "unmatched argument" in lowered:
            proc = _run_list_versions(cli_jar, patches_mpp, package, False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(
            "list-versions failed for "
            f"{package}: {detail[-1] if detail else 'no output'}"
        )

    entries = parse_versions(proc.stdout, package)
    target = choose_target(entries, no_beta)
    if target is None:
        raise RuntimeError(
            f"No compatible versions found for {package} in {patches_mpp}"
        )
    return target, entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True, help="Android package name")
    parser.add_argument("--cli-jar", default=str(CLI_JAR))
    parser.add_argument("--patches-mpp", default=str(PATCHES_MPP))
    parser.add_argument(
        "--experimental",
        action="store_true",
        help="Include experimental versions in search",
    )
    parser.add_argument(
        "--no-beta",
        action="store_true",
        help="Exclude beta versions from results",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        target, entries = resolve_target(
            args.cli_jar,
            args.patches_mpp,
            args.package,
            args.experimental,
            args.no_beta,
        )
    except Exception as exc:  # noqa: BLE001 - the build script logs the message
        print(str(exc), file=sys.stderr)
        return 1

    if args.verbose:
        listing = ", ".join(f"{version}({count})" for version, count in entries)
        print(f"[target] {args.package}: {listing}", file=sys.stderr)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())