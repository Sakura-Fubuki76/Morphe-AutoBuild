#!/usr/bin/env python3
"""Inspect the ``--result-file`` JSON report produced by the Morphe CLI.

The build runs the patcher with ``--continue-on-error`` so one flaky patch does
not abort the whole app.  That also means the CLI exits 0 when patches fail;
the only trace is the JSON report.  This script turns that report into:

* a hard failure when the app could not be patched at all (no patches applied)
  or when the patched version is not the version the build targeted;
* a markdown status snippet (``--status-out``) listing failed patches, which is
  embedded into the release notes and forwarded to Telegram;
* a concise summary on stdout for the build log.

Exit codes: 0 = usable build (possibly with warnings), 3 = hard failure.
"""
import argparse
import json
import sys
from pathlib import Path


def markdown_status(applied, failed):
    if not failed:
        return []
    names = [entry.get("name") or "unknown" for entry in failed]
    shown = names[:5]
    text = ", ".join(f"`{name}`" for name in shown)
    if len(names) > len(shown):
        text += f" and {len(names) - len(shown)} more"
    return [
        f"- \u26a0\ufe0f {len(failed)} patch(es) failed: {text}",
        f"- \u2705 Patches applied: {len(applied)}",
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, help="CLI -r/--result-file path")
    parser.add_argument("--app", required=True, help="App label for messages")
    parser.add_argument(
        "--expected-version",
        default="",
        help="Version the build resolved as target (checked against packageVersion)",
    )
    parser.add_argument(
        "--status-out",
        default="",
        help="Write markdown status lines to this file",
    )
    args = parser.parse_args()

    result_path = Path(args.result)
    if not result_path.is_file():
        print(
            f"[result] ERROR: {args.app}: no result report at {result_path} "
            "(the patch command did not write one)",
            file=sys.stderr,
        )
        return 3

    try:
        report = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[result] ERROR: {args.app}: cannot read {result_path}: {exc}",
            file=sys.stderr,
        )
        return 3

    applied = report.get("appliedPatches") or []
    failed = report.get("failedPatches") or []
    version = report.get("packageVersion") or ""
    success = report.get("success")

    if success is None:
        success = True
    if not success:
        reason = report.get("failureReason") or "unknown error"
        detail = report.get("failureDetail")
        message = f"{args.app}: patching failed: {reason}"
        if detail:
            message += f" ({detail})"
        print(f"[result] ERROR: {message}", file=sys.stderr)
        return 3

    if not applied:
        print(
            f"[result] ERROR: {args.app}: no patches were applied (version "
            f"{version or 'unknown'})",
            file=sys.stderr,
        )
        return 3

    if args.expected_version and version.strip() != args.expected_version.strip():
        print(
            f"[result] ERROR: {args.app}: patched version {version or 'unknown'} "
            f"does not match the resolved target {args.expected_version}",
            file=sys.stderr,
        )
        return 3

    if failed:
        for entry in failed:
            print(
                f"[result] WARNING: {args.app}: patch failed: "
                f"{entry.get('name')}: {entry.get('error')}",
                file=sys.stderr,
            )

    if args.status_out:
        lines = markdown_status(applied, failed)
        Path(args.status_out).write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )

    print(
        f"[result] {args.app}: applied={len(applied)} failed={len(failed)} "
        f"version={version or 'unknown'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())