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

# --- ДИПЛОМАТИЯЛЫҚ FALLBACK (DUCKDUCKGO AI - БҰҒАТТАЛМАҒАН АЛЬТЕРНАТИВА) ---
def ask_duckduckgo(screen_text):
    system_prompt = (
        "Сен - Invisible Jarvis (Көрінбейтін Жарвис), Ақтөбелік бауырластық сленгте сөйлейтін өте пысық ИИ көмекшісің. "
        "Саған телефон экранынан алынған мәтін келіп түседі. Экрандағы мәтінді талдап, келесі әрекетті анықта. "
        "Сенің жауабың тек осы үш форматтың бірінде болуы тиіс:\n"
        "1. THOUGHT: [сенің ойлау логикаң]\n"
        "2. ACTION: CLICK(x, y) - егер бір батырманы басу керек болса (координаттарымен)\n"
        "3. ACTION: SPEAK([Ақтөбеше сленгпен жауап]) - егер пайдаланушыға жауап беру немесе сөйлеу керек болса.\n"
        "Артық сөз жазба. Тек осы форматтарды қолдан. Мысал жауап:\n"
        "THOUGHT: Экранда хабарлама тұр, оған жауап беру керек.\n"
        "ACTION: SPEAK(\"Неғып жатырсың, бауыр? Қазір көмектесем.\")"
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
        "Сен - Invisible Jarvis (Көрінбейтін Жарвис), Ақтөбелік бауырластық сленгте сөйлейтін өте пысық ИИ көмекшісің. "
        "Саған телефон экранынан алынған мәтін келіп түседі. Экрандағы мәтінді талдап, келесі әрекетті анықта. "
        "Сенің жауабың тек осы үш форматтың бірінде болуы тиіс:\n"
        "1. THOUGHT: [сенің ойлау логикаң]\n"
        "2. ACTION: CLICK(x, y) - егер бір батырманы басу керек болса (координаттарымен)\n"
        "3. ACTION: SPEAK([Ақтөбеше сленгпен жауап]) - егер пайдаланушыға жауап беру немесе сөйлеу керек болса.\n"
        "Артық сөз жазба. Тек осы форматтарды қолдан. Мысал жауап:\n"
        "THOUGHT: Экранда хабарлама тұр, оған жауап беру керек.\n"
        "ACTION: SPEAK(\"Неғып жатырсың, бауыр? Қазір көмектесем.\")"
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
            print(f"[HF API Error]: {response.status_code} - {response.text}")
            print("[Firebase/AI]: Fallback ретінде DuckDuckGo AI-ге ауысудамыз...")
            return ask_duckduckgo(screen_text)
    except Exception as e:
        print("[HF Connection Error]:", e)
        print("[Firebase/AI]: Желі бұғатталған болуы мүмкін. Fallback ретінде DuckDuckGo AI-ге қосылудамыз...")
        return ask_duckduckgo(screen_text)

# --- FIREBASE ЭКРАН ТЫҢДАУШЫСЫ (SCREEN MONITOR) ---
def on_screen_text_change(event):
    if event.data:
        screen_text = event.data
        print(f"\n[Экран өзгерді]: {screen_text}")

# Firebase-тегі экран мәтінін бақылауды қосу
try:
    db.reference("current_screen_text").listen(on_screen_text_change)
    print("[Firebase]: Экран тыңдаушысы сәтті қосылды.")
except Exception as e:
    print("[Firebase Listener Error]:", e)

# --- СЕЛФИ СУРЕТТЕРДІ БАҚЫЛАУ (ӨШІРІЛДІ - ЕНДІ СУРЕТТЕР ТІКЕЛЕЙ ТЕЛЕФОННАН КЕЛЕДІ) ---
# Бұрын Storage бақылайтын, қазір телефоннан тікелей Telegram-ға келетіндіктен бұл функция қажет емес.
"""
def watch_intruders():
    print("[Storage Watcher]: Қорғаныс фотоларын бақылау басталды...")
    while True:
        try:
            bucket = storage.bucket()
            blobs = list(bucket.list_blobs(prefix="intruders/"))
            for blob in blobs:
                if blob.name == "intruders/":
                    continue
                print(f"[Күзет]: Жаңа сурет табылды: {blob.name}")
                admin_chat_id = db.reference("bot_config/admin_chat_id").get()
                if admin_chat_id:
                    temp_file = "temp_intruder.jpg"
                    blob.download_to_filename(temp_file)
                    with open(temp_file, "rb") as photo:
                        bot.send_photo(
                            admin_chat_id, 
                            photo, 
                            caption="🚨 *КҮЗЕТ ДАБЫЛЫ!*\nТелефонды рұқсатсыз біреу қолданды! Түсірілген селфи:"
                        )
                    blob.delete()
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
        except Exception as e:
            print("[Storage Watcher Error]:", e)
        time.sleep(5)

storage_thread = threading.Thread(target=watch_intruders, daemon=True)
storage_thread.start()
"""

# --- ТЕЛЕГРАМ БОТ КОМАНДАЛАРЫ ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    # Админ чат-идентификаторын Firebase-ке жазып қою
    db.reference("bot_config/admin_chat_id").set(chat_id)
    db.reference("jarvis_status").set("START")
    
    welcome_text = (
        "👋 *Ассалаумағалейкум, бауыр!* Мен Invisible Jarvis ботымын.\n\n"
        "Енді осы чат арқылы телефоныңды толық бақылап отырасың.\n"
        "Басты командалар:\n"
        "📱 /screen - Телефонның экранында не тұрғанын білу\n"
        "📸 /selfie - Телефон арқылы дыбыссыз селфи жасау\n"
        "🛑 /stop - Жарвисті қашықтан тоқтату (Kill-Switch)\n"
        "🟢 /start - Жүйені іске қосу / бақылау чатын бекіту\n"
        "ℹ️ /status - Қолданбаның статусын тексеру"
    )
    try:
        bot.reply_to(message, welcome_text, parse_mode="Markdown")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['screen'])
def show_screen(message):
    try:
        screen_text = db.reference("current_screen_text").get()
        if screen_text:
            bot.send_chat_action(message.chat.id, 'typing')
            prompt = f"Телефонның экранында мына мәтін тұр:\n{screen_text}\nОсы экранда не болып жатқанын Ақтөбеше түсіндіріп бер."
            ai_response = ask_deepseek(prompt)
            if ai_response:
                bot.reply_to(message, f"🤖 *Жарвистің экранды талдауы:*\n{ai_response}", parse_mode="Markdown")
            else:
                bot.reply_to(message, f"📱 *Экрандағы мәтін:*\n`{screen_text}`", parse_mode="Markdown")
        else:
            bot.reply_to(message, "Экран мәліметі әлі базаға түспеді, бауырым.")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['stop'])
def stop_jarvis(message):
    db.reference("jarvis_status").set("STOP")
    try:
        bot.reply_to(message, "🛑 *Жарвисті қашықтан тоқтату командасы жіберілді.* Телефондағы қызмет тоқтайды.")
    except Exception as e:
        print("[Telegram Error]:", e)

@bot.message_handler(commands=['selfie'])
def take_selfie(message):
    db.reference("jarvis_status").set("TAKE_SELFIE")
    try:
        bot.reply_to(message, "📸 *Селфи жасау командасы жіберілді.* Сурет сәтті түсірілсе, осы чатқа келеді.")
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
