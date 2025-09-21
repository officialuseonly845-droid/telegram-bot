imimport os
import openai
import requests
from flask import Flask, request

# Load keys from environment variables
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

        # Call ChatGPT
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": user_text}]
        )
        answer = response["choices"][0]["message"]["content"]

        # Send reply to Telegram
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": answer
        })

    return {"ok": True}

@app.route("/")
def home():
    return "Bot is running!"
