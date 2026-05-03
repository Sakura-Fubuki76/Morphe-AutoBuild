#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI_JAR = ROOT / "tools" / "morphe-cli.jar"
PATCHES_MPP = ROOT / "tools" / "patches.mpp"
BUILD_DIR = ROOT / "build"

_VERSION_COUNT_LINE = re.compile(r"^\s*(\S+)\s+\(\d+\s+patch(?:es)?\)")


def parse_list_versions(output, package):
    """Parse ``list-versions`` output — stable only."""
    versions = []
    in_section = False
    for line in output.splitlines():
        if line.startswith("Package name: ") and package in line:
            in_section = True
            continue
        if line.startswith("Package name: "):
            in_section = False
            continue
        if in_section:
            m = _VERSION_COUNT_LINE.match(line)
            if m:
                versions.append(m.group(1))
    return versions


def version_key(v):
    """Split version into numeric parts for natural sorting."""
    parts = re.split(r"[-._]", v)
    key = []
    for p in parts:
        try:
            key.append((0, int(p)))
        except ValueError:
            key.append((1, p))
    return key


def get_target_experimental(cli_jar, patches_mpp, package, no_beta=False):
    """Use FindExperimentalVersions to access raw ``patch.compatibility``,
    which includes experimental targets that the legacy
    ``compatiblePackages`` getter strips out."""
    sep = os.pathsep
    classpath = sep.join([str(cli_jar), str(BUILD_DIR)])
    helper_class = str(BUILD_DIR / "FindExperimentalVersions.class")
    if not os.path.exists(helper_class):
        raise RuntimeError(
            f"FindExperimentalVersions.class not found at {helper_class}. "
            "Compile it first: javac -cp tools/morphe-cli.jar "
            "scripts/FindExperimentalVersions.java -d build"
        )

    cmd = [
        "java", "-cp", classpath,
        "FindExperimentalVersions",
        str(patches_mpp), package,
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Java helper failed:\n{result.stderr}")

    versions = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if no_beta:
        versions = [v for v in versions if "beta" not in v.lower()]
    if not versions:
        raise RuntimeError(
            f"No experimental compatible versions found for {package}"
        )

    return versions[-1]


def get_target_stable(cli_jar, patches_mpp, package):
    cmd = [
        "java", "-jar", str(cli_jar),
        "list-versions", str(patches_mpp),
        "--filter-package-names", package,
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"CLI command failed:\n{result.stderr}")

    versions = parse_list_versions(result.stdout, package)
    if not versions:
        raise RuntimeError(
            f"No stable compatible versions found for {package}"
        )

    versions.sort(key=version_key, reverse=True)
    return versions[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--package", required=True, help="Android package name")
    p.add_argument("--cli-jar", default=str(CLI_JAR))
    p.add_argument("--patches-mpp", default=str(PATCHES_MPP))
    p.add_argument("--experimental", action="store_true",
                   help="Include experimental versions in search")
    p.add_argument("--no-beta", action="store_true",
                   help="Exclude beta versions from results")
    args = p.parse_args()

    if args.experimental:
        target = get_target_experimental(
            args.cli_jar, args.patches_mpp, args.package,
            no_beta=args.no_beta,
        )
    else:
        target = get_target_stable(
            args.cli_jar, args.patches_mpp, args.package,
        )

    print(target)


if __name__ == "__main__":
    main()
