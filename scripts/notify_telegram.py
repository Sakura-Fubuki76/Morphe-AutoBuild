import os
import requests

bot_token = os.environ["BOT_TOKEN"]
chat_id = os.environ["CHAT_ID"]
release_tag = os.environ["RELEASE_TAG"]
repo = os.environ["REPO"]

release_url = f"https://github.com/{repo}/releases/tag/{release_tag}"

try:
    with open("build/release-notes.md", "r", encoding="utf-8") as f:
        notes = f.read()
except Exception:
    notes = ""


def parse_release_notes(text):
    """Parse structured markdown release notes into a nested dict."""
    result = {}
    current_section = None
    current_sub = None
    current_items = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("# ") and not stripped.startswith("##"):
            result["title"] = stripped[2:].strip()
        elif stripped.startswith("## "):
            if current_section and current_sub:
                result.setdefault(current_section, {})[current_sub] = current_items
                current_items = []
            current_section = stripped[3:].strip()
            current_sub = None
        elif stripped.startswith("### "):
            if current_section and current_sub:
                result.setdefault(current_section, {})[current_sub] = current_items
                current_items = []
                current_sub = stripped[4:].strip()
            elif current_section:
                current_sub = stripped[4:].strip()
                current_items = []
        elif stripped.startswith("- "):
            current_items.append(stripped[2:].strip())

    if current_section and current_sub:
        result.setdefault(current_section, {})[current_sub] = current_items

    return result


def clean_detail(text):
    """Strip backtick code markers and surrounding whitespace."""
    return text.replace("`", "").strip()


def build_message(parsed, release_tag, release_url):
    lines = []

    lines.append("\U0001F43E \u3054\u4E3B\u4EBA\u69D8\u3001\u65B0\u7248\u672C\u304C\u30EA\u30EA\u30FC\u30B9\u3055\u308C\u307E\u3057\u305F\u3088\uFF01 \U0001F43E")
    lines.append(f"\u2728 <b>Morphe Bulids</b> \u306E\u65B0\u88D9\u5B50\u304C\u3067\u304D\u307E\u3057\u305F\uFF5E \u2728")
    lines.append("")

    available = parsed.get("Available Files", {})
    if available:
        lines.append("\u2501" * 18)
        lines.append("\U0001F4E6 <b>\u672C\u6B21\u66F4\u65B0\u5185\u5BB9</b>")
        for app_label, details in available.items():
            if not details:
                continue
            line = clean_detail(details[0])
            is_failed = "build failed" in line.lower()
            if is_failed:
                lines.append(f"  \u274C <b>{app_label}</b> \u2014 \u6784\u5EFA\u5931\u8D25")
            else:
                lines.append(f"  \u2705 <b>{app_label}</b>: {line}")
        lines.append("")

    sources = parsed.get("Patch Sources", {})
    if sources:
        lines.append("\u2501" * 18)
        lines.append("\U0001F527 <b>\u8865\u4E01\u6E90</b>")
        for name, details in sources.items():
            if not details:
                continue
            line = clean_detail(details[0])
            lines.append(f"  \u2022 <b>{name}</b>: {line}")
        lines.append("")

    lines.append(f"\U0001F517 <a href='{release_url}'>\U0001F449 \u70B9\u51FB\u5E26\u56DE\u5BB6 \u306B\u3083\u3093 \u2661 \U0001F448</a>")

    return "\n".join(lines)


parsed = parse_release_notes(notes)
text = build_message(parsed, release_tag, release_url)

resp = requests.post(
    f"https://api.telegram.org/bot{bot_token}/sendMessage",
    json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    },
)
print(resp.status_code, resp.text)
