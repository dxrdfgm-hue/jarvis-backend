import os
import time
import threading
import requests
import telebot
import firebase_admin
from firebase_admin import credentials, db, storage

# --- БАПТАУЛАР / CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8908205939:AAFB-YufkUFK3WPxSlYMBTJtuNUoj_y8lGI")
HF_API_URL = "https://api-inference.huggingface.co/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
HF_TOKEN = os.environ.get("HF_TOKEN", "") # Hugging Face токені (міндетті емес, бірақ лимиттерді көбейтеді)

# Firebase әдепкі сілтемелері (serviceAccountKey.json арқылы жұмыс істейді)
DEFAULT_DB_URL = "https://invisible-jarvis-default-rtdb.firebaseio.com/"
DEFAULT_STORAGE_BUCKET = "invisible-jarvis.firebasestorage.app"

# Телеграм ботты баптау
bot = telebot.TeleBot(BOT_TOKEN)
telebot.apihelper.CONNECT_TIMEOUT = 60
telebot.apihelper.READ_TIMEOUT = 60

# Қауіпсіздік үшін Hugging Face бұғаттауларын айналып өтуге арналған Прокси (Cloudflare Worker)
proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "")
if proxy_url:
    if not proxy_url.endswith("/"):
        proxy_url += "/"
    telebot.apihelper.API_URL = proxy_url + "bot{0}/{1}"
    print(f"[Telegram Bot]: Бұлттық прокси қолданылуда: {proxy_url}")

# --- FIREBASE ИНИЦИАЛИЗАЦИЯСЫ ---
import json

cred_path = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
firebase_creds_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "")

if firebase_creds_json:
    print("[Firebase]: FIREBASE_SERVICE_ACCOUNT жүйелік айнымалысы табылды. Сол арқылы қосылуда...")
    try:
        cred_dict = json.loads(firebase_creds_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred, {
            'databaseURL': os.environ.get('FIREBASE_DB_URL', DEFAULT_DB_URL),
            'storageBucket': os.environ.get('FIREBASE_STORAGE_BUCKET', DEFAULT_STORAGE_BUCKET)
        })
    except Exception as e:
        print("[ERROR]: FIREBASE_SERVICE_ACCOUNT арқылы қосылу сәтсіз:", e)
elif os.path.exists(cred_path):
    print("[Firebase]: serviceAccountKey.json файлы табылды. Қосылуда...")
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'databaseURL': os.environ.get('FIREBASE_DB_URL', DEFAULT_DB_URL),
        'storageBucket': os.environ.get('FIREBASE_STORAGE_BUCKET', DEFAULT_STORAGE_BUCKET)
    })
else:
    print("[WARN]: serviceAccountKey.json немесе FIREBASE_SERVICE_ACCOUNT табылмады. Әдепкі логинмен қосылуда...")
    try:
        firebase_admin.initialize_app(options={
            'databaseURL': os.environ.get('FIREBASE_DB_URL', DEFAULT_DB_URL),
            'storageBucket': os.environ.get('FIREBASE_STORAGE_BUCKET', DEFAULT_STORAGE_BUCKET)
        })
    except Exception as e:
        print("[ERROR]: Firebase қосылу сәтсіз аяқталды:", e)


def ensure_jarvis_config():
    try:
        config_ref = db.reference("jarvis_config")
        current_config = config_ref.get()
        default_config = {
            "voice_passphrase": "жарвис",
            "auto_reply_enabled": False,
            "digital_shield_enabled": False,
            "geo_fence_enabled": False,
            "geo_fence_home_lat": 0.0,
            "geo_fence_home_lon": 0.0,
            "geo_fence_radius_meters": 300,
            "self_prompt_enabled": False
        }

        if not isinstance(current_config, dict):
            config_ref.set(default_config)
            print("[Firebase]: jarvis_config әдепкі мәндері орнатылды.")
        else:
            missing = {}
            for key, value in default_config.items():
                if key not in current_config:
                    missing[key] = value
            if missing:
                config_ref.update(missing)
                print("[Firebase]: jarvis_config үшін жетіспейтін мәндер қосылды:", list(missing.keys()))
    except Exception as e:
        print("[Config Init Error]: jarvis_config орнатылмады:", e)

# --- ДИПЛОМАТИЯЛЫҚ FALLBACK (DUCKDUCKGO AI - БҰҒАТТАЛМАҒАН АЛЬТЕРНАТИВА) ---
def ask_duckduckgo(screen_text):
    system_prompt = (
        "Сен - JARVIS (Жарвис), Тони Старктың интеллектуалды, жоғары технологиялық ИИ көмекшісісің. "
        "Сен әрқашан пайдаланушымен өте сыпайы, интеллектуалды және сабырлы сөйлесуің керек. Оған әрқашан 'Сэр' деп сөйле. "
        "Саған телефон экранынан алынған мәтін келіп түседі. Экрандағы мәтінді талдап, келесі әрекетті анықта. "
        "Сенің жауабың тек осы форматтардың бірінде болуы тиіс:\n"
        "1. THOUGHT: [сенің ойлау логикаң]\n"
        "2. ACTION: CLICK(x, y) - егер бір батырманы немесе элементті басу керек болса (координаттарымен)\n"
        "3. ACTION: SPEAK([Сэр деп атап, сыпайы жауап, қазақша немесе орысша]) - егер пайдаланушыға жауап беру немесе сөйлеу керек болса. Экран мәліметін талдағанда оны HUD жүйелік диагностика есебі ретінде ұсын (Мысалы: 'Жүйелік сканерлеу: Белсенді терезе - WhatsApp. Сэр, сізге жаңа хабарлама келді.').\n"
        "4. ACTION: OPEN_APP([қолданба атауы]) - егер пайдаланушы қолданбаны ашуды сұраса.\n"
        "5. ACTION: TYPE_TEXT([мәтін]) - егер бір өріске мәтін жазу керек болса.\n"
        "6. ACTION: SWIPE_DOWN() - егер экранды төмен сырғыту керек болса (мысалы, TikTok немесе лентаны парақтау).\n"
        "7. ACTION: SWIPE_UP() - егер экранды жоғары сырғыту керек болса.\n"
        "8. ACTION: GO_BACK() - егер артқа (назад) қайту керек болса.\n"
        "9. ACTION: GO_HOME() - егер басты экранға (домой) шығу керек болса.\n"
        "Артық сөз жазба. Тек осы форматтарды қолдан. Мысал жауап:\n"
        "THOUGHT: Пайдаланушы тикток парақтауды сұрады.\n"
        "ACTION: SWIPE_DOWN()"
    )
    
    prompt = f"System:\n{system_prompt}\n\nUser:\nЭкран мәтіні: {screen_text}\n"
    
    headers_status = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "x-client-event": "1"
    }
    
    try:
        # 1-қадам: VQD токенін алу
        res = requests.get("https://duckduckgo.com/duckchat/v1/status", headers=headers_status, timeout=10)
        vqd = res.headers.get("x-vqd-4")
        if not vqd:
            print("[DuckDuckGo Error]: VQD токені алынбады.")
            return None
            
        # 2-қадам: Чатқа сұраныс жіберу (gpt-4o-mini тегін және бұғатталмаған)
        headers_chat = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "x-vqd-4": vqd,
            "Accept": "text/event-stream"
        }
        
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        res_chat = requests.post("https://duckduckgo.com/duckchat/v1/chat", json=payload, headers=headers_chat, timeout=15)
        if res_chat.status_code == 200:
            lines = res_chat.text.split("\n")
            response_text = ""
            for line in lines:
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        import json
                        data_json = json.loads(data_str)
                        chunk = data_json.get("message", "")
                        if chunk:
                            response_text += chunk
                    except:
                        pass
            return response_text.strip()
        else:
            print(f"[DuckDuckGo API Error]: {res_chat.status_code} - {res_chat.text}")
    except Exception as e:
        print("[DuckDuckGo Connection Error]:", e)
    return None

# --- ИИ (DEEPSEEK-R1) СҰРАНЫСТАРЫ ---
def ask_deepseek(screen_text):
    system_prompt = (
        "Сен - JARVIS (Жарвис), Тони Старктың интеллектуалды, жоғары технологиялық ИИ көмекшісісің. "
        "Сен әрқашан пайдаланушымен өте сыпайы, интеллектуалды және сабырлы сөйлесуің керек. Оған әрқашан 'Сэр' деп сөйле. "
        "Саған телефон экранынан алынған мәтін келіп түседі. Экрандағы мәтінді талдап, келесі әрекетті анықта. "
        "Сенің жауабың тек осы форматтардың бірінде болуы тиіс:\n"
        "1. THOUGHT: [сенің ойлау логикаң]\n"
        "2. ACTION: CLICK(x, y) - егер бір батырманы немесе элементті басу керек болса (координаттарымен)\n"
        "3. ACTION: SPEAK([Сэр деп атап, сыпайы жауап, қазақша немесе орысша]) - егер пайдаланушыға жауап беру немесе сөйлеу керек болса. Экран мәліметін талдағанда оны HUD жүйелік диагностика есебі ретінде ұсын (Мысалы: 'Жүйелік сканерлеу: Белсенді терезе - WhatsApp. Сэр, сізге жаңа хабарлама келді.').\n"
        "4. ACTION: OPEN_APP([қолданба атауы]) - егер пайдаланушы қолданбаны ашуды сұраса.\n"
        "5. ACTION: TYPE_TEXT([мәтін]) - егер бір өріске мәтін жазу керек болса.\n"
        "6. ACTION: SWIPE_DOWN() - егер экранды төмен сырғыту керек болса (мысалы, TikTok немесе лентаны парақтау).\n"
        "7. ACTION: SWIPE_UP() - егер экранды жоғары сырғыту керек болса.\n"
        "8. ACTION: GO_BACK() - егер артқа (назад) қайту керек болса.\n"
        "9. ACTION: GO_HOME() - егер басты экранға (домой) шығу керек болса.\n"
        "Артық сөз жазба. Тек осы форматтарды қолдан. Мысал жауап:\n"
        "THOUGHT: Пайдаланушы тикток парақтауды сұрады.\n"
        "ACTION: SWIPE_DOWN()"
    )
    
    prompt = f"<|system|>\n{system_prompt}\n<|user|>\nЭкран мәтіні: {screen_text}\n<|assistant|>\n"
    
    headers = {}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"
        
    try:
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 250,
                "temperature": 0.7,
                "return_full_text": False
            }
        }
        response = requests.post(HF_API_URL, json=payload, headers=headers, timeout=20)
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                return result[0].get("generated_text", "").strip()
            return str(result)
        else:
            return ask_duckduckgo(screen_text)
    except Exception as e:
        return ask_duckduckgo(screen_text)

# --- ДАУЫСТЫҚ КОМАНДАЛАРҒА АРНАЛҒАН AI ---
def ask_deepseek_command(command, screen_text, history_context=""):
    system_prompt = (
        "Сен - JARVIS (Жарвис), интеллектуалды көмекші. "
        "Пайдаланушының дауыстық командасын қазақша немесе орысша табиғи тілде түсініп, ең дұрысы әрекет түрін таңдауың керек. "
        "Жауап тек мына форматта болуға тиіс:\n"
        "ACTION: OPEN_APP(қолданба атауы)\n"
        "ACTION: TYPE_TEXT(мәтін)\n"
        "ACTION: CLICK(x, y)\n"
        "ACTION: SWIPE_DOWN()\n"
        "ACTION: SWIPE_UP()\n"
        "ACTION: GO_BACK()\n"
        "ACTION: GO_HOME()\n"
        "ACTION: SPEAK(мәтін)\n"
        "ACTION: TAKE_SELFIE()\n"
        "ACTION: TAKE_SCREENSHOT()\n"
        "ACTION: ALARM()\n"
        "ACTION: STOP_ALARM()\n"
        "ACTION: GET_DEVICE_STATUS()\n"
        "ACTION: RECORD_AUDIO(секунд)\n"
        "ACTION: GET_LOCATION()\n"
        "ACTION: STOP()\n"
        "ACTION: WAIT(секунд) - егер әрекет арасында уақытша күту қажет болса.\n"
        "Егер команда бірнеше қадамнан тұрса, әр әрекетті бөлек ACTION жолында ретімен жаз.\n"
        "Егер бір әрекетті анықтап болмайтын болса, тек SPEAK(сөйлем) форматында жауап бер.\n"
        "Мысал:\n"
        "ACTION: OPEN_APP(youtube)\n"
        "ACTION: WAIT(3)\n"
        "ACTION: CLICK(540, 1850)\n"
        "ACTION: TYPE_TEXT(ан)\n"
        "ACTION: WAIT(1)\n"
        "ACTION: CLICK(560, 1890)\n"
        "Мысал: 'Жарвис, WhatsApp аш' -> ACTION: OPEN_APP(whatsapp)\n"
        "Мысал: 'Жарвис, экранды төмен сырғыт' -> ACTION: SWIPE_DOWN()\n"
        "Мысал: 'Жарвис, селфи түсір' -> ACTION: TAKE_SELFIE()\n"
        "Мысал: 'Жарвис, телефонның жағдайы қандай?' -> ACTION: GET_DEVICE_STATUS()\n"
        "Мысал: 'Жарвис, дауысты жаз' -> ACTION: RECORD_AUDIO(10)\n"
        "Егер команда бірнеше әрекетті қамтыса, әр әрекетті бөлек ACTION жолында жазыңыз.\n"
        "Мысал:\n"
        "ACTION: OPEN_APP(youtube)\n"
        "ACTION: WAIT(3)\n"
        "ACTION: CLICK(540, 1850)\n"
        "ACTION: TYPE_TEXT(ан)\n"
        "ACTION: WAIT(1)\n"
        "ACTION: CLICK(560, 1890)\n"
    )
    prompt = (
        f"<|system|>\n{system_prompt}\n<|user|>\n"
        f"{history_context}[Экран: {screen_text}]\n[Команда]: {command}\n<|assistant|>\n"
    )
    headers = {}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"
    try:
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 250,
                "temperature": 0.3,
                "return_full_text": False
            }
        }
        response = requests.post(HF_API_URL, json=payload, headers=headers, timeout=25)
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                return result[0].get("generated_text", "").strip()
            return str(result)
        else:
            return ask_duckduckgo(command)
    except Exception as e:
        return ask_duckduckgo(command)

import re

def parse_ai_response(response_text):
    # Regex to find SWIPE_DOWN() or SWIPE_UP()
    swipe_match = re.search(r'ACTION:\s*SWIPE_(DOWN|UP)\b', response_text, re.IGNORECASE)
    if swipe_match:
        return "SWIPE", swipe_match.group(1).upper()
        
    # Regex to find GO_BACK() or GO_HOME()
    nav_match = re.search(r'ACTION:\s*GO_(BACK|HOME)\b', response_text, re.IGNORECASE)
    if nav_match:
        return "NAV", nav_match.group(1).upper()

    # Regex to find OPEN_APP("...") or OPEN_APP(...)
    open_app_match = re.search(r'ACTION:\s*OPEN_APP\((["\']?)(.*?)\1\)', response_text, re.IGNORECASE)
    if open_app_match:
        return "OPEN_APP", open_app_match.group(2).strip()

    # Regex to find TYPE_TEXT("...") or TYPE_TEXT(...)
    type_match = re.search(r'ACTION:\s*TYPE_TEXT\((["\']?)(.*?)\1\)', response_text, re.IGNORECASE | re.DOTALL)
    if type_match:
        return "TYPE_TEXT", type_match.group(2).strip()

    # Regex to find SPEAK("...") or SPEAK(...)
    speak_match = re.search(r'ACTION:\s*SPEAK\((["\']?)(.*?)\1\)', response_text, re.IGNORECASE | re.DOTALL)
    if speak_match:
        return "SPEAK", speak_match.group(2).strip()
        
    # Regex to find CLICK(x, y)
    click_match = re.search(r'ACTION:\s*CLICK\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)', response_text, re.IGNORECASE)
    if click_match:
        try:
            x = float(click_match.group(1))
            y = float(click_match.group(2))
            return "CLICK", (x, y)
        except ValueError:
            pass

    # Regex to find special actions
    special_match = re.search(r'ACTION:\s*(TAKE_SELFIE|TAKE_SCREENSHOT|ALARM|STOP_ALARM|GET_DEVICE_STATUS|RECORD_AUDIO|GET_LOCATION|STOP)\s*(?:\(\s*([0-9]+)\s*\))?', response_text, re.IGNORECASE)
    if special_match:
        action = special_match.group(1).upper()
        arg = special_match.group(2)
        if action == "RECORD_AUDIO":
            try:
                return "RECORD_AUDIO", int(arg) if arg else 10
            except ValueError:
                return "RECORD_AUDIO", 10
        return action, arg or ""
            
    # If no ACTION format is matched, check if there's any speak fallback
    clean_text = response_text
    if "THOUGHT:" in clean_text:
        lines = clean_text.split("\n")
        clean_lines = [line for line in lines if not line.strip().upper().startswith("THOUGHT:")]
        clean_text = "\n".join(clean_lines).strip()
    
    clean_text = re.sub(r'^ACTION:\s*', '', clean_text, flags=re.IGNORECASE).strip()
    if (clean_text.startswith('"') and clean_text.endswith('"')) or (clean_text.startswith("'") and clean_text.endswith("'")):
        clean_text = clean_text[1:-1].strip()
        
    if clean_text:
        return "SPEAK", clean_text
    return None, None


def parse_ai_response_sequence(response_text):
    if not response_text:
        return []

    actions = []
    action_pattern = re.compile(
        r'ACTION:\s*(OPEN_APP|TYPE_TEXT|CLICK|SWIPE_DOWN|SWIPE_UP|GO_BACK|GO_HOME|SPEAK|TAKE_SELFIE|TAKE_SCREENSHOT|ALARM|STOP_ALARM|GET_DEVICE_STATUS|RECORD_AUDIO|GET_LOCATION|STOP|WAIT)\s*(?:\(\s*(.*?)\s*\))?',
        re.IGNORECASE | re.DOTALL
    )

    for match in action_pattern.finditer(response_text):
        action = match.group(1).upper()
        raw_value = match.group(2) or ""

        if action == "CLICK":
            click_match = re.match(r'([0-9.]+)\s*,\s*([0-9.]+)', raw_value)
            if click_match:
                try:
                    x = float(click_match.group(1))
                    y = float(click_match.group(2))
                    actions.append(("CLICK", (x, y)))
                except ValueError:
                    continue
        elif action == "RECORD_AUDIO":
            try:
                actions.append(("RECORD_AUDIO", int(raw_value) if raw_value else 10))
            except ValueError:
                actions.append(("RECORD_AUDIO", 10))
        elif action == "WAIT":
            try:
                actions.append(("WAIT", float(raw_value) if raw_value else 1.0))
            except ValueError:
                actions.append(("WAIT", 1.0))
        else:
            actions.append((action, raw_value.strip()))

    return actions


def dispatch_action(action_type, action_value):
    try:
        if action_type == "WAIT":
            time.sleep(float(action_value) if action_value else 1.0)
            return True
        if action_type == "SPEAK":
            db.reference("commands/speak").set(action_value)
            return True
        if action_type == "OPEN_APP":
            db.reference("commands/open_app").set(action_value)
            db.reference("commands/speak").set(f"{action_value} ашылуда.")
            return True
        if action_type == "TYPE_TEXT":
            db.reference("commands/last_type").set({"text": action_value, "timestamp": int(time.time() * 1000)})
            db.reference("commands/speak").set("Жазып жатырмын, бауырым.")
            return True
        if action_type == "SWIPE_DOWN" or action_type == "SWIPE_UP":
            direction = action_type.split("_")[-1]
            db.reference("commands/last_scroll").set({"direction": direction, "timestamp": int(time.time() * 1000)})
            return True
        if action_type == "GO_BACK" or action_type == "GO_HOME":
            nav_action = "BACK" if action_type == "GO_BACK" else "HOME"
            db.reference("commands/last_nav").set({"action": nav_action, "timestamp": int(time.time() * 1000)})
            return True
        if action_type == "CLICK":
            x, y = action_value
            db.reference("commands/last_click").set({"x": x, "y": y, "timestamp": int(time.time() * 1000)})
            db.reference("commands/speak").set("Қазір басамын, бауырым.")
            return True
        if action_type == "TAKE_SELFIE":
            db.reference("jarvis_status").set("TAKE_SELFIE")
            db.reference("commands/speak").set("Селфи түсіру басталды.")
            return True
        if action_type == "TAKE_SCREENSHOT":
            db.reference("jarvis_status").set("TAKE_SCREENSHOT")
            db.reference("commands/speak").set("Скриншот түсіру басталды.")
            return True
        if action_type == "ALARM":
            db.reference("jarvis_status").set("ALARM")
            db.reference("commands/speak").set("Сирена қосылды.")
            return True
        if action_type == "STOP_ALARM":
            db.reference("jarvis_status").set("START")
            db.reference("commands/speak").set("Сирена тоқтатылды.")
            return True
        if action_type == "GET_DEVICE_STATUS":
            db.reference("jarvis_status").set("GET_DEVICE_STATUS")
            db.reference("commands/speak").set("Құрылғы статусын жинап жатырмын.")
            return True
        if action_type == "RECORD_AUDIO":
            db.reference("commands/record_duration").set(action_value)
            db.reference("jarvis_status").set("RECORD_AUDIO")
            db.reference("commands/speak").set("Дыбыс жазу басталды.")
            return True
        if action_type == "GET_LOCATION":
            db.reference("jarvis_status").set("GET_LOCATION")
            db.reference("commands/speak").set("Орналасқан жер анықталуда.")
            return True
        if action_type == "STOP":
            db.reference("jarvis_status").set("STOP")
            db.reference("commands/speak").set("Жарвис тоқтатылды.")
            return True
        return False
    except Exception as e:
        print("[Dispatch Error]:", e)
        return False


def execute_action_sequence(actions, command, screen_text, ai_response):
    success = False
    last_action_type = "UNKNOWN"
    last_action_value = ""

    for idx, (action_type, action_value) in enumerate(actions):
        if dispatch_action(action_type, action_value):
            if action_type != "WAIT":
                success = True
                last_action_type = action_type
                last_action_value = str(action_value)
        if idx < len(actions) - 1:
            time.sleep(1.4)

    append_voice_history(command, screen_text, ai_response or "", last_action_type, last_action_value, success)

# --- SELF-LEARNING КОНТЕКСТІ ---
def build_voice_history_context(limit: int = 6):
    try:
        history_ref = db.reference("voice_history")
        history_data = history_ref.order_by_child("timestamp").limit_to_last(limit).get()
        if isinstance(history_data, dict):
            sorted_items = sorted(history_data.items(), key=lambda item: item[1].get("timestamp", 0))
            lines = []
            for _, entry in sorted_items:
                cmd = entry.get("command", "")
                action_type = entry.get("action_type", "")
                action_value = entry.get("action_value", "")
                if cmd:
                    lines.append(f"- Команда: {cmd} | Әрекет: {action_type} | Нәтиже: {action_value}")
            if lines:
                return "Жадыдан соңғы дауыстық командалар мен олардың әрекеттері:\n" + "\n".join(lines) + "\n\n"
    except Exception as e:
        print("[Self Learning Error]:", e)
    return ""


def append_voice_history(command, screen_text, ai_response, action_type, action_value, success: bool):
    if not success:
        return
    try:
        data = {
            "command": command,
            "screen_text": screen_text,
            "ai_response": ai_response,
            "action_type": action_type,
            "action_value": action_value,
            "success": True,
            "timestamp": int(time.time() * 1000)
        }
        db.reference("voice_history").push(data)
    except Exception as e:
        print("[Voice History Error]:", e)

# --- FIREBASE ЭКРАН ТЫҢДАУШЫСЫ (SCREEN MONITOR) ---
def on_screen_text_change(event):
    if event.data:
        screen_text = event.data
        print(f"\n[Экран өзгерді]: {screen_text}")

try:
    db.reference("current_screen_text").listen(on_screen_text_change)
    print("[Firebase]: Экран тыңдаушысы сәтті қосылды.")
except Exception as e:
    print("[Firebase Listener Error]:", e)

# --- FIREBASE ХАБАРЛАМАЛАРДЫ БАҚЫЛАУ (NOTIFICATION MONITOR) ---
last_notif_timestamp = int(time.time() * 1000)

def on_notification_change(event):
    global last_notif_timestamp
    if not event.data or not isinstance(event.data, dict): return
    try:
        data = event.data
        timestamp = data.get("timestamp", 0)
        if timestamp <= last_notif_timestamp: return
        last_notif_timestamp = timestamp
        app = data.get("app", "")
        title = data.get("title", "")
        text = data.get("text", "")
        notif_speech = f"Бауырым, {app} арқылы {title} жазды: {text}"
        db.reference("commands/speak").set(notif_speech)
        admin_chat_id = db.reference("bot_config/admin_chat_id").get()
        if admin_chat_id:
            bot.send_message(admin_chat_id, f"🔔 *Жаңа хабарлама:* {title}\n💬 {text}", parse_mode="Markdown")
    except Exception as e:
        print("[Notification Listener Error]:", e)

try:
    db.reference("notifications/last_received").listen(on_notification_change)
except Exception as e:
    print("[Firebase Notification Listener Error]:", e)

# --- FIREBASE ДАУЫСТЫҚ КОМАНДА ТЫҢДАУШЫСЫ (VOICE COMMAND MONITOR) ---
last_processed_voice_timestamp = int(time.time() * 1000)

def on_voice_command_change(event):
    global last_processed_voice_timestamp
    if not event.data or not isinstance(event.data, dict): return
    try:
        command = event.data.get("command", "").strip()
        timestamp = event.data.get("timestamp", 0)
        if not command or timestamp <= last_processed_voice_timestamp: return
        last_processed_voice_timestamp = timestamp
        screen_text = db.reference("current_screen_text").get() or ""
        history_context = build_voice_history_context()
        ai_response = ask_deepseek_command(command, screen_text, history_context)
        actions = parse_ai_response_sequence(ai_response) if ai_response else []

        if actions:
            threading.Thread(target=execute_action_sequence, args=(actions, command, screen_text, ai_response or ""), daemon=True).start()
        else:
            append_voice_history(command, screen_text, ai_response or "", "UNKNOWN", "", False)
    except Exception as e:
        print("[Voice Command Listener Error]:", e)

try:
    db.reference("voice_commands").listen(on_voice_command_change)
except Exception as e:
    print("[Firebase Voice Listener Error]:", e)

# --- ТЕЛЕГРАМ БОТ КОМАНДАЛАРЫ ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    db.reference("bot_config/admin_chat_id").set(message.chat.id)
    bot.reply_to(message, "👋 Ассалаумағалейкум, бауырым! Жарвис іске қосылды.")
    list_commands(message)

@bot.message_handler(commands=['screen'])
def show_screen(message):
    screen_text = db.reference("current_screen_text").get()
    if screen_text:
        ai_response = ask_deepseek(f"Экранда мына мәтін тұр:\n{screen_text}\nБұл не?")
        bot.reply_to(message, f"🤖 *Талдау:*\n{ai_response}", parse_mode="Markdown")

@bot.message_handler(commands=['stop'])
def stop_jarvis(message):
    db.reference("jarvis_status").set("STOP")
    bot.reply_to(message, "🛑 Тоқтатылды.")

@bot.message_handler(commands=['selfie'])
def take_selfie(message):
    try:
        db.reference("jarvis_status").set("TAKE_SELFIE")
        bot.reply_to(message, "📸 *Селфи жасау командасы жіберілді.* Сурет сәтті түсірілсе, осы чатқа келеді, бауырым.")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['screenshot'])
def take_screenshot(message):
    try:
        db.reference("jarvis_status").set("TAKE_SCREENSHOT")
        bot.reply_to(message, "📸 *Скриншот түсіру командасы жіберілді.* Дайын болғанда осы чатқа келеді, бауырым!")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['alarm'])
def start_alarm(message):
    try:
        db.reference("jarvis_status").set("ALARM")
        bot.reply_to(message, "🚨 *Ұрыға қарсы дабыл іске қосылды!* Телефон барынша шыңылдап сирена қосады.\nТоқтату үшін /stop_alarm деп жаз, немесе телефоннан Jarvis қолданбасын аш, бауырым!")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['stop_alarm'])
def stop_alarm(message):
    try:
        db.reference("jarvis_status").set("START")
        bot.reply_to(message, "✅ *Дабыл тоқтатылды!* Телефон тынышталды, бауырым.")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['device'])
def get_device_status(message):
    try:
        db.reference("device_info").delete()
        db.reference("jarvis_status").set("GET_DEVICE_STATUS")
        bot.reply_to(message, "⏳ *Құрылғы мәліметтері жиналуда...* Сәл күте тұр, бауырым.")
        
        for _ in range(12):
            time.sleep(0.5)
            info = db.reference("device_info").get()
            if info and isinstance(info, dict) and "timestamp" in info:
                battery = info.get("battery", "Белгісіз")
                thermal = info.get("thermal", "Белгісіз")
                wifi = info.get("wifi", "Белгісіз")
                ram = info.get("ram", "Белгісіз")
                
                msg = (
                    "📱 *Құрылғы статусы (Device Status):*\n\n"
                    f"🔋 *Батарея:* `{battery}`\n"
                    f"🌡 *Температура:* `{thermal}`\n"
                    f"📶 *Желі (Wi-Fi):* `{wifi}`\n"
                    f"🧠 *Жад (RAM):* `{ram}`\n"
                    "Бауырым, телефонның жағдайы осындай!"
                )
                bot.send_message(message.chat.id, msg, parse_mode="Markdown")
                return
                
        bot.reply_to(message, "⚠️ *Қате:* Телефоннан жауап келмеді. Бауырым, телефон қосулы және интернетте екенін тексерші.")
    except Exception as e:
        print("[Telegram Error]:", e)
        bot.reply_to(message, f"❌ Қате орын алды: {e}")

@bot.message_handler(commands=['record'])
def record_audio(message):
    try:
        args = message.text.split()
        duration = 10
        if len(args) > 1:
            try:
                duration = int(args[1])
                if duration <= 0 or duration > 60:
                    duration = 10
            except ValueError:
                pass
        db.reference("commands/record_duration").set(duration)
        db.reference("jarvis_status").set("RECORD_AUDIO")
        bot.reply_to(message, f"🎙 *Дауыс жазу басталды ({duration} сек).* Айналадағы дыбыс жазылып, осы чатқа жіберіледі, бауырым.")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['location'])
def get_location(message):
    try:
        db.reference("device_location").delete()
        db.reference("jarvis_status").set("GET_LOCATION")
        bot.reply_to(message, "⏳ *Телефон координаттары анықталуда...* Сәл күте тұр, бауырым.")
        
        for _ in range(12):
            time.sleep(0.5)
            loc = db.reference("device_location").get()
            if loc and isinstance(loc, dict) and "timestamp" in loc:
                if "error" in loc:
                    bot.reply_to(message, "❌ *Қате:* Телефонда орналасқан жерді анықтау мүмкіндігі сөндірулі немесе рұқсат берілмеген.")
                    return
                lat = loc.get("latitude")
                lon = loc.get("longitude")
                acc = loc.get("accuracy", 0.0)
                
                maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
                msg = (
                    "📍 *Телефонның орналасқан жері (Location):*\n\n"
                    f"🌐 *Ендік (Latitude):* `{lat}`\n"
                    f"🌐 *Бойлық (Longitude):* `{lon}`\n"
                    f"🎯 *Дәлдік:* `~{acc} метр`\n\n"
                    f"🗺 [Google Maps сілтемесі]({maps_url})\n\n"
                    "Бауырым, телефон дәл осы жерде тұр!"
                )
                bot.send_message(message.chat.id, msg, parse_mode="Markdown")
                return
                
        bot.reply_to(message, "⚠️ *Қате:* Телефоннан жауап келмеді. Бауырым, телефон қосулы және интернетте екенін тексерші.")
    except Exception as e:
        print("[Telegram Error]:", e)
        bot.reply_to(message, f"❌ Қате орын алды: {e}")

@bot.message_handler(commands=['open'])
def open_app_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        bot.reply_to(message, "ℹ️ Қолданба атауын жазыңыз, мысалы: /open whatsapp")
        return
    app_name = args[1].strip()
    db.reference("commands/open_app").set(app_name)
    bot.reply_to(message, f"📱 *{app_name}* қолданбасы ашылуда...")

@bot.message_handler(commands=['click'])
def click_command(message):
    args = message.text.split()
    if len(args) != 3:
        bot.reply_to(message, "ℹ️ Қағида: /click x y\nМысалы: /click 500 1200")
        return
    try:
        x = float(args[1])
        y = float(args[2])
        db.reference("commands/last_click").set({"x": x, "y": y, "timestamp": int(time.time() * 1000)})
        bot.reply_to(message, f"👆 Экрандағы ({x}, {y}) координатына басу командасы жіберілді.")
    except ValueError:
        bot.reply_to(message, "❌ Координаталарды дұрыс енгізіңіз: /click 500 1200")

@bot.message_handler(commands=['type'])
def type_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        bot.reply_to(message, "ℹ️ Қағида: /type Бұл жерде мәтін\nМысалы: /type Сәлем, бауырым")
        return
    text = args[1].strip()
    db.reference("commands/last_type").set({"text": text, "timestamp": int(time.time() * 1000)})
    bot.reply_to(message, f"✍️ Мәтін жазу командасы жіберілді:\n`{text}`", parse_mode="Markdown")

@bot.message_handler(commands=['swipe'])
def swipe_command(message):
    args = message.text.split(maxsplit=1)
    direction = args[1].strip().lower() if len(args) > 1 else ""
    if direction not in ["up", "down"]:
        bot.reply_to(message, "ℹ️ Қағида: /swipe up немесе /swipe down")
        return
    db.reference("commands/last_scroll").set({"direction": direction.upper(), "timestamp": int(time.time() * 1000)})
    bot.reply_to(message, f"⬆️⬇️ Экранды {direction} бағытта сырғыту командасы жіберілді.")

@bot.message_handler(commands=['back'])
def back_command(message):
    db.reference("commands/last_nav").set({"action": "BACK", "timestamp": int(time.time() * 1000)})
    bot.reply_to(message, "🔙 Артқа қайту командасы жіберілді.")

@bot.message_handler(commands=['home'])
def home_command(message):
    db.reference("commands/last_nav").set({"action": "HOME", "timestamp": int(time.time() * 1000)})
    bot.reply_to(message, "🏠 Басты экранға шығу командасы жіберілді.")

@bot.message_handler(commands=['say'])
def say_command(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        bot.reply_to(message, "ℹ️ Қағида: /say Бұл жерде мәтін\nМысалы: /say Телефонды тексеріп жатырмын")
        return
    text = args[1].strip()
    db.reference("commands/speak").set(text)
    bot.reply_to(message, f"🔉 Телефонға сөйлеу командасы жіберілді: `{text}`", parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def list_commands(message):
    help_text = (
        "🤖 *Invisible Jarvis — Қолжетімді командалар:* \n\n"
        "📸 *Камера мен Скриншот:*\n"
        "👉 /selfie — Алдыңғы камерадан селфи түсіріп жіберу\n"
        "👉 /screenshot — Телефон экранының скриншотын жіберу\n\n"
        "🚨 *Қауіпсіздік пен Дабыл (Anti-Theft):*\n"
        "👉 /alarm — Максималды дыбыспен сирена қосу (бесшумныйда да істейді)\n"
        "👉 /stop_alarm — Сиренаны тоқтату\n\n"
        "🎙 *Тыңдау мен Дыбыс:*\n"
        "👉 /record — Айналадағы дыбысты 10 секунд жазып, дауыстық хатпен жіберу\n\n"
        "📍 *Орналасқан жері (GPS):*\n"
        "👉 /location — Телефонның нақты координаттары мен Google Maps сілтемесін алу\n\n"
        "📱 *Қашықтан экрандық басқару:*\n"
        "👉 /open [қолданба атауы] — Қолданбаны ашу\n"
        "👉 /click x y — Экрандағы координатқа басу\n"
        "👉 /type [мәтін] — Фокустағы өріске мәтін жазу\n"
        "👉 /swipe up/down — Экранды сырғыту\n"
        "👉 /back — Артқа қайту\n"
        "👉 /home — Басты экранға шығу\n"
        "👉 /say [мәтін] — Телефонға сөйлеу\n\n"
        "⚙️ *Жүйе статусы:*\n"
        "👉 /device — Батарея, температура, RAM және Wi-Fi мәліметтерін алу\n"
        "👉 /status — Жарвистің қазіргі жүйелік статусын тексеру\n"
        "👉 /stop — Жарвисті қашықтан толық сөндіру (Kill-Switch)\n\n"
        "Бауырым, керекті команданы таңда немесе сұрағыңды жай мәтінмен жаз!"
    )
    try:
        bot.reply_to(message, help_text, parse_mode="Markdown")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['status'])
def check_status(message):
    status = db.reference("jarvis_status").get()
    try:
        bot.reply_to(message, f"ℹ️ *Ағымдағы статус:* `{status}`")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    user_text = message.text
    if user_text.lower().strip() in ["көмек", "помощь", "командалар", "help", "меню", "команды", "команда"]:
        list_commands(message)
        return
        
    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except Exception as e:
        print("[Telegram Error]:", e)
    
    # ИИ-ге экрандағы мәтінді контекст ретінде беру
    screen_text = db.reference("current_screen_text").get()
    context = ""
    if screen_text:
        context = f"[Телефон экранындағы ағымдағы мәтін: {screen_text}]\n"
        
    ai_response = ask_deepseek(f"{context}[Пайдаланушы хабарламасы]: {user_text}")
    try:
        if ai_response:
            action_type, action_val = parse_ai_response(ai_response)
            if action_type == "SPEAK":
                bot.reply_to(message, action_val)
            elif action_type == "OPEN_APP":
                db.reference("commands/open_app").set(action_val)
                bot.reply_to(message, f"📱 *Әрекет басталды:* {action_val} қолданбасы ашылуда...")
                db.reference("commands/speak").set(f"{action_val} қолданбасын ашамын, бауырым.")
            elif action_type == "TYPE_TEXT":
                type_data = {
                    "text": action_val,
                    "timestamp": int(time.time() * 1000)
                }
                db.reference("commands/last_type").set(type_data)
                bot.reply_to(message, f"✍️ *Әрекет басталды:* Мәтін жазылуда: `{action_val}`")
                db.reference("commands/speak").set("Жазып жатырмын, бауырым.")
            elif action_type == "GESTURE":
                gesture_data = {
                    "action": action_val,
                    "timestamp": int(time.time() * 1000)
                }
                db.reference("commands/last_gesture").set(gesture_data)
                bot.reply_to(message, f"👈 *Әрекет басталды:* Экран қимылы орындалуда: `{action_val}`")
            elif action_type == "SYSTEM_CONTROL":
                control_data = {
                    "action": action_val,
                    "timestamp": int(time.time() * 1000)
                }
                db.reference("commands/system_control").set(control_data)
                bot.reply_to(message, f"⚙️ *Әрекет басталды:* Жүйелік реттеу: `{action_val}`")
            elif action_type == "CLICK":
                x, y = action_val
                click_data = {
                    "x": x,
                    "y": y,
                    "timestamp": int(time.time() * 1000)
                }
                db.reference("commands/last_click").set(click_data)
                bot.reply_to(message, f"👆 *Әрекет басталды:* Экрандағы ({x}, {y}) координаттары басылуда...")
                # Телефонға да дыбыстық белгі беру
                db.reference("commands/speak").set("Қазір басамын, бауырым.")
            else:
                bot.reply_to(message, ai_response)
        else:
            bot.reply_to(message, "Ақтөбеде байланыс нашар болып тұр, бауырым. Сәлден кейін қайталашы.")
    except Exception as e:
        print("[Telegram Error]:", e)

def run_telegram_bot():
    print("[Telegram Bot]: Бот фонда іске қосылуда...")
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            print("[Telegram Bot Error]:", e)
            time.sleep(5)

# Бот пен веб-интерфейсті іске қосу
if __name__ == "__main__":
    ensure_jarvis_config()

    # Телеграм ботты фонда қосу
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()
    
    # Шағын веб-интерфейс (Gradio) ашу, Hugging Face Spaces-ке қажет
    import gradio as gr
    
    def check_status():
        try:
            status = db.reference("jarvis_status").get()
            return f"Жүйе қосулы. Ағымдағы статус: {status}"
        except Exception as e:
            return f"Қате: {e}"

    with gr.Blocks(title="Invisible Jarvis") as demo:
        gr.Markdown("## 🤖 Invisible Jarvis Backend Server")
        gr.Markdown("Сервер фонда табысты жұмыс істеп тұр.")
        status_output = gr.Textbox(label="Статус", value=check_status())
        refresh_btn = gr.Button("Жаңарту")
        refresh_btn.click(fn=check_status, outputs=status_output)

    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)
