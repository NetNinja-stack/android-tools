import json
import os
import re
import time
from datetime import datetime
from typing import Dict, Tuple, Optional

import requests
import schedule

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

    cookie_match = re.search(r"(?i)\b(?:-H|--header)\s+['\"]Cookie:\s*([^'\"]+)['\"]", blob)
    ua_match = re.search(r"(?i)\b(?:-H|--header)\s+['\"]User-Agent:\s*([^'\"]+)['\"]", blob)

    cookies = _parse_cookie_header(cookie_match.group(1)) if cookie_match else None
    ua = ua_match.group(1) if ua_match else None
    return cookies, ua


def load_cookies_and_ua() -> Tuple[Dict[str, str], str]:
    candidates_json = ["cookies.json", os.path.join(".secret", "cookies.json")]
    for p in candidates_json:
        jar = _load_cookies_from_json(p)
        if jar:
            print(f"✅ Cookies: loaded from {p} ({len(jar)} entries)")
            break
    else:
        jar = None

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

    ua_from_curl = None
    if jar is None or not jar:
        for p in ["curl.txt", os.path.join(".secret", "curl.txt")]:
            cookies_from_curl, ua_from_curl = _extract_from_curl_txt(p)
            if cookies_from_curl:
                jar = cookies_from_curl
                print(f"✅ Cookies: extracted from {p} ({len(jar)} entries)")
                break

    if not jar:
        raise RuntimeError("Cookies not found. Provide cookies.json OR cookies.txt OR curl.txt next to the script.")

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
        ua = (
            "Mozilla/5.0 (Linux; Android 13; Pixel 5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/118.0.0.0 Mobile Safari/537.36"
        )
        print("⚠️ UA fallback in use. Better to supply your real UA in ua.txt.")

    return jar, ua


# === TikTok helpers ===

def extract_aweme_id(url: str) -> Optional[str]:
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
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

def load_next_link(links_file: str = "links.txt") -> Optional[str]:
    """Load the first link from links.txt file."""
    if not os.path.exists(links_file):
        print("❌ File links.txt not found.")
        return None
    
    try:
        with open(links_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    return line
    except Exception as e:
        print(f"⚠️ Error reading links.txt: {e}")
    
    return None


def remove_used_link(link: str, links_file: str = "links.txt") -> bool:
    """Remove the used link from links.txt file."""
    if not os.path.exists(links_file):
        print("❌ File links.txt not found.")
        return False
    
    try:
        with open(links_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        with open(links_file, "w", encoding="utf-8") as f:
            for line in lines:
                if line.strip() != link.strip():
                    f.write(line)
        
        print(f"✅ Удалена ссылка из links.txt")
        return True
    except Exception as e:
        print(f"⚠️ Error removing link from links.txt: {e}")
        return False


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

    lines.append("")  # blank line

    with open(out_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ Appended {len(comments)} comments to {out_path}")


# === Main ===

def process_once(cookies: Dict[str, str], ua: str) -> bool:
    """Process one link from links.txt and remove it after completion."""
    links_file = "links.txt"
    
    link = load_next_link(links_file)
    if not link:
        print("⚠️ Нет ссылок в links.txt")
        return False
    
    print(f"\n🔗 Обработка ссылки: {link}")
    aweme_id = extract_aweme_id(link)
    if not aweme_id:
        print("⚠️ Не удалось извлечь aweme_id из ссылки. Удаляю её.")
        remove_used_link(link, links_file)
        return False
    
    session = build_session(ua, cookies)
    try:
        comments = fetch_comments(session, aweme_id, referer=link)
        save_to_database(link, comments, out_path=OUTPUT_FILE)
        remove_used_link(link, links_file)
        print("✅ Ссылка успешно обработана и удалена из lists.txt")
        return True
    except requests.RequestException as e:
        print(f"⚠️ Ошибка сети: {e}")
        return False
    except Exception as e:
        print(f"⚠️ Ошибка при обработке: {e}")
        return False


def scheduled_task(cookies: Dict[str, str], ua: str):
    """Wrapper for scheduled task."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🕐 Начало запланированной задачи")
    process_once(cookies, ua)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🕐 Задача завершена\n")


def main():
    try:
        cookies, ua = load_cookies_and_ua()
    except Exception as e:
        print("❌", e)
        return
    
    # Расписание: запуск два раза в день (9:00 и 21:00)
    schedule.every().day.at("09:00").do(scheduled_task, cookies=cookies, ua=ua)
    schedule.every().day.at("21:00").do(scheduled_task, cookies=cookies, ua=ua)
    
    print("\n✅ Планировщик активирован!")
    print("📅 Задачи запланированы на:")
    print("   • 09:00")
    print("   • 21:00")
    print("💡 Программа будет работать в фоне. Для остановки нажмите Ctrl+C\n")
    
    # Основной цикл планировщика
    while True:
        schedule.run_pending()
        time.sleep(60)  # Проверка каждую минуту


if __name__ == "__main__":
    main()
