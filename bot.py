import os
import openai
import requests
from flask import Flask, request

# Load secrets from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

app = Flask(__name__)
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        user_text = data["message"].get("text", "")

        # Always respond to /start
        if user_text == "/start":
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Hi! I’m your BELLUGA bot. Mention 'BELLUGA' in your message and I’ll respond!"
            })
            return {"ok": True}

        # If message contains "BELLUGA" exactly, send fixed greeting
        if user_text.strip().upper() == "BELLUGA":
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": "Hi! I am BELLUGA, ask me anything 😎"
            })
            return {"ok": True}

        # If message contains "BELLUGA" and extra text → treat as question
        if "BELLUGA" in user_text.upper():
            # Ask ChatGPT
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": user_text}]
            )
            answer = response["choices"][0]["message"]["content"]

            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": answer
            })

    return {"ok": True}

@app.route("/")
def home():
    return "Bot is running!"
