#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import sys
import time
import logging

import requests

APKMIRROR_CONFIGS = {
    "youtube": {
        "package": "com.google.android.youtube",
        "org": "google-inc",
        "name": "youtube",
        "prefer_apk": True, 
    },
    "reddit": {
        "package": "com.reddit.frontpage",
        "org": "redditinc",
        "name": "reddit",
    },
    "twitter": {
        "package": "com.twitter.android",
        "org": "x-corp",
        "name": "twitter",
        "release_prefix": "x",
    },
}

APKMIRROR_BASE = "https://www.apkmirror.com"

APKPURE_API = "https://api.pureapk.com/m/v3/cms/app_version"
DOWNLOAD_URL_RE = re.compile(
    r"(X?APKJ).."
    r"(https?://(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}"
    r"\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&/=]*))",
    re.DOTALL,
)
VERSION_RE = re.compile(r"(\d{1,6}\.\d{1,3}\.\d{1,3})")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [apk_dl] %(message)s",
    datefmt="%H:%M:%S",
)
def _build_apkpure_headers(arch="arm64-v8a,armeabi-v7a,armeabi,x86,x86_64"):
    return {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Pro)",
        "x-cv": "3172501",
        "x-sv": "29",
        "x-abis": arch,
        "x-gp": "1",
    }


def _apkmirror_session():
    """Create a requests session that impersonates Chrome (anti-bot)."""
    try:
        from curl_cffi import requests as curl_requests
        from curl_cffi.requests.impersonate import DEFAULT_CHROME
        return curl_requests.Session(impersonate=DEFAULT_CHROME)
    except ImportError:
        logging.warning("curl_cffi not available, using plain requests (may be blocked)")
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        })
        return s


def _check_cloudflare(resp):
    """Raise RuntimeError if the response is a Cloudflare challenge page."""
    if resp.status_code == 403 and "Just a moment" in resp.text[:500]:
        raise RuntimeError("Blocked by Cloudflare anti-bot challenge")


def get_latest_version(config):
    """Return the latest non-alpha/non-beta version from APKMirror."""
    from bs4 import BeautifulSoup

    s = _apkmirror_session()
    url = f"{APKMIRROR_BASE}/apk/{config['org']}/{config['name']}/"

    resp = s.get(url)
    _check_cloudflare(resp)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    app_rows = soup.find_all("div", class_="appRow")
    version_pattern = re.compile(r"\d+(\.\d+)*(-[a-zA-Z0-9]+(\.\d+)*)*")

    for row in app_rows:
        title_el = row.find("h5", class_="appRowTitle")
        if not title_el or not title_el.a:
            continue
        version_text = title_el.a.text.strip()
        low = version_text.lower()
        if "alpha" in low or "beta" in low:
            continue
        match = version_pattern.search(version_text)
        if not match:
            continue

        version = match.group()
        parts = version.split(".")
        base_parts = [p for p in parts if p.isdigit()]
        if not base_parts:
            continue
        base_version = ".".join(base_parts)

        build_match = re.search(r"\((\d+)\)", version_text)
        if build_match:
            return f"{base_version}({build_match.group(1)})"

        return base_version

    return None


def get_download_link(version, config):
    """Resolve the final download URL for a specific version on APKMirror.

    Returns (url, file_type, actual_version) or raises RuntimeError.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import quote

    s = _apkmirror_session()

    build_number = None
    build_format = None

    paren_match = re.search(r"\((\d+)\)$", version)
    if paren_match:
        build_number = paren_match.group(1)
        build_format = "parentheses"
        clean_version = version[:paren_match.start()]
    else:
        build_match = re.search(r"\s+build\s+(\d+)$", version, re.IGNORECASE)
        if build_match:
            build_number = build_match.group(1)
            build_format = "build_suffix"
            clean_version = version[:build_match.start()]
        else:
            clean_version = version

    version_parts = clean_version.split(".")

    # Strip piko-specific tags (e.g. 0-release-ripped → 0-release) for
    # additional APKMirror URL candidates.
    _stripped_parts = [re.sub(r'-(?:ripped|patched|mod)(?=-|$)', '', p) for p in version_parts]
    _has_stripped = _stripped_parts != version_parts

    found_soup = None
    correct_page = False

    for i in range(len(version_parts), 0, -1):
        current_ver_str = "-".join(version_parts[:i])

        if build_number and i == len(version_parts):
            if build_format == "build_suffix":
                current_ver_str = f"{current_ver_str}-build-{build_number}"
            else:
                parts = list(version_parts[:i])
                parts[-1] = parts[-1] + build_number
                current_ver_str = "-".join(parts)

        release_name = config.get("release_prefix", config["name"])
        encoded_app = quote(config["name"], safe="")
        encoded_rel = quote(release_name, safe="")

        url_patterns = []
        url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_rel}-{current_ver_str}-release/")
        if release_name != config["name"]:
            url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_app}-{current_ver_str}-release/")
        url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_rel}-{current_ver_str}/")
        if release_name != config["name"]:
            url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_app}-{current_ver_str}/")

        # Also try URLs with piko tags stripped from version parts
        if _has_stripped:
            alt_ver_str = "-".join(_stripped_parts[:i])
            if alt_ver_str != current_ver_str:
                url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_rel}-{alt_ver_str}-release/")
                if release_name != config["name"]:
                    url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_app}-{alt_ver_str}-release/")
                url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_rel}-{alt_ver_str}/")
                if release_name != config["name"]:
                    url_patterns.append(f"{APKMIRROR_BASE}/apk/{config['org']}/{encoded_app}/{encoded_app}-{alt_ver_str}/")

        url_patterns = list(dict.fromkeys(url_patterns))

        for url in url_patterns:
            logging.info(f"Checking: {url}")
            try:
                resp = s.get(url)
                _check_cloudflare(resp)
                if resp.status_code != 200:
                    continue

                soup = BeautifulSoup(resp.content, "html.parser")
                page_text = soup.get_text()

                version_checks = [
                    clean_version,
                    clean_version.replace(".", "-"),
                    current_ver_str,
                    ".".join(version_parts[:i]),
                ]
                if _has_stripped:
                    stripped_dot = ".".join(_stripped_parts[:i])
                    version_checks.append(stripped_dot)
                    version_checks.append(stripped_dot.replace(".", "-"))
                if build_number:
                    if build_format == "build_suffix":
                        version_checks.append(f"{clean_version} build {build_number}")
                    else:
                        version_checks.append(f"{clean_version}({build_number})")

                for check in version_checks:
                    if check and check in page_text:
                        correct_page = True
                        found_soup = soup
                        logging.info(f"Found correct version page: {resp.url}")
                        break

                if correct_page:
                    break

                if found_soup is None:
                    found_soup = soup

            except Exception as e:
                logging.warning(f"Error checking {url}: {e}")
                continue

        if correct_page:
            break

    if not found_soup:
        raise RuntimeError(f"No APKMirror page found for {config['name']} {version}")

    if not correct_page:
        logging.warning(f"Using fallback page (may list multiple versions)")

    rows = found_soup.find_all("div", class_="table-row headerFont")
    prefer_apk = config.get("prefer_apk", False)

    variants = [] 
    for row in rows:
        row_text = row.get_text()
        if re.search(r"\d+(\.\d+)+", row_text):
            sub_url = row.find("a", class_="accent_color")
            if sub_url:
                is_bundle = any(
                    kw in row_text.lower() for kw in ("bundle", "apkm", "apks")
                )
                variants.append((is_bundle, APKMIRROR_BASE + sub_url["href"]))

    if not variants:
        raise RuntimeError(f"No variant found for {config['name']} {version}")

    if prefer_apk:
        apk_variants = [v for v in variants if not v[0]]
        if apk_variants:
            download_page_url = apk_variants[0][1]
            logging.info("Selected APK variant (skipping BUNDLE/APKM)")
        else:
            download_page_url = variants[0][1]
            logging.info("No APK variant found, using fallback")
    else:
        download_page_url = variants[0][1]

    resp = s.get(download_page_url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    dl_button = soup.find("a", class_="downloadButton")
    if not dl_button:
        raise RuntimeError("No download button on variant page")

    final_page_url = APKMIRROR_BASE + dl_button["href"]
    resp = s.get(final_page_url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    link = soup.find("a", id="download-link")
    if not link:
        raise RuntimeError("No download link on final page")

    final_url = APKMIRROR_BASE + link["href"]

    low = final_url.lower()
    if low.endswith(".apkm") or "/apkm/" in low or ".apkm?" in low:
        file_type = "APKM"
    elif low.endswith(".apks") or "/apks/" in low or ".apks?" in low:
        file_type = "APKS"
    elif low.endswith(".xapk") or "/xapk/" in low or ".xapk?" in low:
        file_type = "XAPK"
    elif low.endswith(".apk") or ".apk?" in low:
        file_type = "APK"
    else:
        file_type = _guess_type_from_head(s, final_url)

    return final_url, file_type, clean_version


def fetch_from_apkpure(session, package, target_version=None):
    """Call the APKPure versions API and extract download URL + version.

    APKPure returns a protobuf stream where version entries and download
    URLs are interleaved.  This function matches them by ordinal position
    (version[0] → url[0], version[1] → url[1], …).
    """
    headers = _build_apkpure_headers()
    url = f"{APKPURE_API}?hl=en-US&package_name={package}"
    logging.info(f"APKPure fallback: {url}")
    resp = session.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"APKPure API returned {resp.status_code}")

    body = resp.text

    versions = [(m.start(), m.group(1)) for m in VERSION_RE.finditer(body)]
    all_urls = [(m.start(), m.group(1), m.group(2)) for m in DOWNLOAD_URL_RE.finditer(body)]

    if not versions or not all_urls:
        raise RuntimeError("Could not find version / download URL in APKPure response")

    if target_version:
        for vpos, ver in versions:
            if ver == target_version:
                for upos, xapk_flag, dl_url in all_urls:
                    if upos > vpos:
                        file_type = "XAPK" if xapk_flag == "XAPKJ" else "APK"
                        logging.info(f"APKPure matched target version {ver}")
                        return dl_url, ver, file_type

        # Try fallback: strip non-numeric suffixes (e.g. 11.91.0-release-ripped.0 → 11.91.0)
        base_match = VERSION_RE.search(target_version)
        if base_match:
            base_ver = base_match.group(1)
            if base_ver != target_version:
                logging.info(f"Exact version {target_version} not found, trying base version {base_ver}")
                for vpos, ver in versions:
                    if ver == base_ver:
                        for upos, xapk_flag, dl_url in all_urls:
                            if upos > vpos:
                                file_type = "XAPK" if xapk_flag == "XAPKJ" else "APK"
                                logging.warning(f"Using base version {base_ver} as fallback for {target_version}")
                                return dl_url, base_ver, file_type

        available = ", ".join(sorted(set(v for _, v in versions), reverse=True)[:10])
        raise RuntimeError(
            f"Version {target_version} not found in APKPure response. "
            f"Available versions: {available}"
        )

    _, version = versions[0]
    _, xapk_flag, dl_url = all_urls[0]
    file_type = "XAPK" if xapk_flag == "XAPKJ" else "APK"

    return dl_url, version, file_type


def _guess_type_from_head(session, url):
    """Use a HEAD request to guess file type from Content-Disposition header."""
    try:
        resp = session.head(url, allow_redirects=True, timeout=30)
        disp = resp.headers.get("Content-Disposition", "")
        low = disp.lower()
        for t in ("apkm", "apks", "xapk"):
            if f".{t}" in low:
                return t.upper()
        return "APK"
    except Exception:
        return "APK"


def download_file(session, download_url, output_path):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    logging.info(f"Downloading {download_url[:120]}...")
    resp = session.get(
        download_url,
        stream=True,
        timeout=600,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed: {resp.status_code}")

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f"\r  {pct}% ({downloaded:,}/{total:,})", end="", file=sys.stderr)
    print("", file=sys.stderr)
    logging.info(f"Saved to {output_path} ({downloaded:,} bytes)")
    return output_path


def main():
    p = argparse.ArgumentParser(description="Download APKs from APKMirror (APKPure fallback)")
    p.add_argument("--app", required=True, help="App identifier (youtube/reddit/twitter)")
    p.add_argument("--package", required=True, help="Android package name")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--include-beta", action="store_true")
    p.add_argument("--name", default="", help="Display name for logging")
    p.add_argument("--target-version", default="", help="Target version (from CLI list-versions)")
    args = p.parse_args()

    name = args.name or args.app
    target_version = args.target_version or None

    if not target_version:
        logging.warning("No --target-version given. APKMirror requires a specific version; "
                        "using APKPure latest as fallback.")
    else:
        logging.info(f"Target version: {target_version}")

    shutil.rmtree(args.out_dir, ignore_errors=True)
    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.dirname(args.meta) or ".", exist_ok=True)

    config = APKMIRROR_CONFIGS.get(args.app)

    dl_url = None
    version = None
    file_type = None
    source = "apkpure"

    if config and target_version:
        try:
            dl_url, file_type, version = get_download_link(target_version, config)
            source = "apkmirror"
        except Exception as e:
            logging.warning(f"APKMirror failed: {e}")

    if not dl_url:
        logging.info("Falling back to APKPure...")
        apkpure_session = requests.Session()
        dl_url, version, file_type = fetch_from_apkpure(
            apkpure_session, args.package, target_version=target_version,
        )
        source = "apkpure"
        time.sleep(1)

    is_apkmirror = source == "apkmirror"
    download_session = _apkmirror_session() if is_apkmirror else requests.Session()

    if not is_apkmirror and not download_session.headers.get("User-Agent"):
        download_session.headers.update(_build_apkpure_headers())

    ext = f".{file_type.lower()}" if file_type else ".apk"
    output_path = os.path.join(args.out_dir, f"{args.app}{ext}")
    download_file(download_session, dl_url, output_path)

    track = "beta" if args.include_beta else "public-stable"
    with open(args.meta, "w", encoding="utf-8") as f:
        json.dump({
            "source": source,
            "app": name,
            "package": args.package,
            "version": version,
            "file_type": file_type,
            "url": dl_url,
            "track": track,
        }, f, indent=2)

    logging.info(f"Done: {name} v{version} (via {source})")


if __name__ == "__main__":
    main()
