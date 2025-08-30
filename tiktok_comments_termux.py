#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok comments downloader for Termux (Android) without Selenium.

How it works:
- You manually export cookies + User-Agent from your browser session on tiktok.com
- Put them into files near this script: cookies.json OR cookies.txt and ua.txt
- The script reads links from links.txt (one TikTok video URL per line)
- It saves all found comments (and replies) into database.txt

Files the script uses (any of the cookie formats is fine):
  - links.txt            — required. One TikTok URL per line
  - cookies.json         — optional. Either [{"name":"...","value":"..."}, ...] or {"name":"value", ...}
  - cookies.txt          — optional. Raw header like: "name=value; name2=value2; ..."
  - curl.txt             — optional. Paste full "Copy as cURL" (from DevTools) here; script will extract Cookie + UA
  - ua.txt               — optional. User-Agent string

Minimal Termux setup:
  pkg update && pkg upgrade -y
  pkg install python -y
  pip install --upgrade pip
  pip install requests
  termux-setup-storage
  mkdir -p ~/storage/shared/tiktok_scraper && cd ~/storage/shared/tiktok_scraper
  # put this script + links.txt + cookies/ua files here, then run:
  python tiktok_comments_termux.py

Notes:
- Keep cookies fresh. If you start getting 0 comments or 403, refresh cookies from your browser.
- Use the SAME User-Agent as when you exported cookies.
- Respect TikTok ToS and laws in your country; use for personal/educational purposes.
"""

import json
import os
import re
import time
from datetime import datetime
from typing import Dict, Tuple, Optional

import requests

# === Settings ===
MAX_COMMENTS = int(os.environ.get("MAX_COMMENTS", 100000))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", 50))  # TikTok web limits 50
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 20))
SLEEP_BETWEEN_PAGES = float(os.environ.get("SLEEP_BETWEEN_PAGES", 0.6))
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "database.txt")

# === Helpers to load cookies & UA ===

def _parse_cookie_header(header: str) -> Dict[str, str]:
    jar: Dict[str, str] = {}
    for part in header.split(";"):
        if not part.strip():
            continue
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        jar[name.strip()] = value.strip()
    return jar


def _load_cookies_from_json(path: str) -> Optional[Dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            # format: {"name": "value", ...}
            return {str(k): str(v) for k, v in data.items()}
        if isinstance(data, list):
            # format: [{"name":"...","value":"..."}, ...]
            jar = {}
            for item in data:
                name = item.get("name")
                value = item.get("value")
                if name is not None and value is not None:
                    jar[str(name)] = str(value)
            return jar
    except Exception:
        pass
    return None


def _extract_from_curl_txt(path: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Read a file that contains a cURL command (Chrome DevTools: Copy as cURL) and extract Cookie + UA."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = f.read()
    except Exception:
        return None, None

    # Try to find -H 'Cookie: ...' or --header "Cookie: ..."
    cookie_match = re.search(r"(?i)\b(?:-H|--header)\s+['\"]Cookie:\s*([^'\"]+)['\"]", blob)
    ua_match = re.search(r"(?i)\b(?:-H|--header)\s+['\"]User-Agent:\s*([^'\"]+)['\"]", blob)

    cookies = _parse_cookie_header(cookie_match.group(1)) if cookie_match else None
    ua = ua_match.group(1) if ua_match else None
    return cookies, ua


def load_cookies_and_ua() -> Tuple[Dict[str, str], str]:
    # Order of attempts for cookies
    candidates_json = ["cookies.json", os.path.join(".secret", "cookies.json")]
    for p in candidates_json:
        jar = _load_cookies_from_json(p)
        if jar:
            print(f"✅ Cookies: loaded from {p} ({len(jar)} entries)")
            break
    else:
        jar = None

    # Try cookies.txt (raw header)
    if jar is None:
        for p in ["cookies.txt", os.path.join(".secret", "cookies.txt")]:
            if os.path.exists(p):
                try:
                    raw = open(p, "r", encoding="utf-8").read().strip()
                    if raw.lower().startswith("cookie:"):
                        raw = raw.split(":", 1)[1].strip()
                    jar = _parse_cookie_header(raw)
                    if jar:
                        print(f"✅ Cookies: loaded from {p} ({len(jar)} entries)")
                        break
                except Exception:
                    pass

    # Try curl.txt
    ua_from_curl = None
    if jar is None or not jar:
        for p in ["curl.txt", os.path.join(".secret", "curl.txt")]:
            cookies_from_curl, ua_from_curl = _extract_from_curl_txt(p)
            if cookies_from_curl:
                jar = cookies_from_curl
                print(f"✅ Cookies: extracted from {p} ({len(jar)} entries)")
                break

    if not jar:
        raise RuntimeError(
            "Cookies not found. Provide cookies.json OR cookies.txt OR curl.txt next to the script."
        )

    # Load UA
    ua = None
    for p in ["ua.txt", os.path.join(".secret", "ua.txt")]:
        if os.path.exists(p):
            try:
                ua = open(p, "r", encoding="utf-8").read().strip()
                if ua:
                    print(f"✅ UA loaded from {p}")
                    break
            except Exception:
                pass

    if ua is None and ua_from_curl:
        ua = ua_from_curl
        print("✅ UA extracted from curl.txt")

    if ua is None:
        # Fallback UA: Chrome on Android
        ua = (
            "Mozilla/5.0 (Linux; Android 13; Pixel 5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 Mobile Safari/537.36"
        )
        print("⚠️ UA fallback in use. Better to supply your real UA in ua.txt.")

    return jar, ua


# === TikTok helpers ===

def extract_aweme_id(url: str) -> Optional[str]:
    # Typical forms: https://www.tiktok.com/@user/video/7222222222222222222
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    # Fallback: last numeric segment
    tail = url.split("?")[0].rstrip("/").split("/")[-1]
    return tail if tail.isdigit() else None


def build_session(ua: str, cookies: Dict[str, str]) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.tiktok.com",
        }
    )
    # Install cookies into the session
    for name, value in cookies.items():
        s.cookies.set(name, value, domain=".tiktok.com")
    return s


def fetch_replies(session: requests.Session, aweme_id: str, parent_cid: str, referer: str) -> list:
    url = "https://www.tiktok.com/api/comment/list/reply/"
    headers = {"Referer": referer}
    params = {
        "aid": "1988",
        "aweme_id": aweme_id,
        "cursor": "0",
        "count": "50",
        "comment_id": parent_cid,
    }

    replies = []
    seen_ids = set()
    while True:
        r = session.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        try:
            data = r.json()
        except Exception:
            break

        items = data.get("comments") or []
        if not items:
            break

        for rpl in items:
            rid = str(rpl.get("cid"))
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            r_user = rpl.get("user", {})
            replies.append(
                {
                    "cid": rid,
                    "text": rpl.get("text"),
                    "author_nickname": r_user.get("nickname"),
                    "author_unique_id": r_user.get("unique_id"),
                    "likes": rpl.get("digg_count"),
                    "reply_count": rpl.get("reply_comment_total"),
                    "parent_cid": parent_cid,
                }
            )

        if not data.get("has_more"):
            break
        params["cursor"] = str(data.get("cursor", 0))
        time.sleep(SLEEP_BETWEEN_PAGES)

    return replies


def fetch_comments(session: requests.Session, aweme_id: str, referer: str) -> list:
    url = "https://www.tiktok.com/api/comment/list/"
    headers = {"Referer": referer}
    params = {
        "aid": "1988",
        "aweme_id": aweme_id,
        "cursor": "0",
        "count": str(BATCH_SIZE),
    }

    all_comments = []
    seen_ids = set()
    total_received = 0

    while True:
        r = session.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 403:
            print("❌ 403 Forbidden — usually means cookies/UA are stale. Refresh them.")
            break
        try:
            data = r.json()
        except Exception as e:
            print("⚠️ JSON parse error:", e)
            break

        comments = data.get("comments") or []
        if not comments:
            if total_received == 0:
                print("⚠️ No comments returned. Check if the video is accessible and cookies are valid.")
            break

        for c in comments:
            cid = str(c.get("cid"))
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            user = c.get("user", {})
            comment_obj = {
                "cid": cid,
                "text": c.get("text"),
                "author_nickname": user.get("nickname"),
                "author_unique_id": user.get("unique_id"),
                "likes": c.get("digg_count"),
                "reply_count": c.get("reply_comment_total"),
            }
            all_comments.append(comment_obj)

            # Fetch all replies if there are more than inline provided
            inline_replies = c.get("reply_comment") or []
            inline_count = len(inline_replies)
            total_replies = comment_obj["reply_count"] or 0
            if total_replies > inline_count:
                replies = fetch_replies(session, aweme_id, cid, referer)
                all_comments.extend(replies)

        total_received += len(comments)
        print(f"📥 Page got {len(comments)} (total so far: {total_received})")

        if not data.get("has_more") or total_received >= MAX_COMMENTS:
            break

        params["cursor"] = str(data.get("cursor", 0))
        time.sleep(SLEEP_BETWEEN_PAGES)

    return all_comments


# === Persistence ===

def save_to_database(link: str, comments: list, out_path: str = OUTPUT_FILE) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append(f"=== Комментарии от {now} ===")
    lines.append(f"tik tok link: {link}")
    lines.append(f"✅ Найдено комментариев: {len(comments)}\n")

    for c in comments:
        nickname = c.get("author_unique_id") or c.get("author_nickname") or "Unknown"
        text = c.get("text") or ""
        text = text.replace("\n", " ")
        lines.append(f"— (@{nickname}): {text}")

    lines.append("")  # blank line between entries

    with open(out_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ Appended {len(comments)} comments to {out_path}")


# === Main ===

def main():
    links_file = "links.txt"
    if not os.path.exists(links_file):
        print("❌ File links.txt not found. Create it with one TikTok URL per line.")
        return

    with open(links_file, "r", encoding="utf-8") as f:
        links = [line.strip() for line in f if line.strip()]

    if not links:
        print("❌ links.txt is empty.")
        return

    try:
        cookies, ua = load_cookies_and_ua()
    except Exception as e:
        print("❌", e)
        return

    for link in links:
        print(f"\n🔗 Processing: {link}")
        aweme_id = extract_aweme_id(link)
        if not aweme_id:
            print("⚠️ Could not extract aweme_id from link. Skipping.")
            continue

        session = build_session(ua, cookies)
        try:
            comments = fetch_comments(session, aweme_id, referer=link)
            save_to_database(link, comments, out_path=OUTPUT_FILE)
        except requests.RequestException as e:
            print(f"⚠️ Network error for {link}: {e}")
        except Exception as e:
            print(f"⚠️ Error while processing {link}: {e}")
        time.sleep(0.8)


if __name__ == "__main__":
    main()
