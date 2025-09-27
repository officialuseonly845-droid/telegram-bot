import os
import re
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import threading

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Health Check ----------
STATUS_MESSAGES = {"server_alive": "Server alive ✅"}

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(STATUS_MESSAGES["server_alive"].encode())

    def log_message(self, format, *args):
        pass  # Suppress default logging

def start_health_server():
    port = int(os.environ.get("PORT", 5000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"✅ Health server running on 0.0.0.0:{port}")
    server.serve_forever()

# ---------- Telegram Bot ----------
active_groups = set()
LINK_PATTERN = re.compile(r'(https?://\S+|www\.\S+)')

async def active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_groups.add(chat_id)
    await update.message.reply_text("✅ Bot is now active in this group!")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_groups.discard(chat_id)
    await update.message.reply_text("🛑 Bot is now inactive in this group.")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if chat_id not in active_groups:
        return
    text = update.message.text or ""
    if not LINK_PATTERN.search(text):
        try:
            await update.message.delete()
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"{user.mention_html()} PLEASE DON'T TALK OFFTOPIC HERE 🚨",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error deleting message: {e}")

# ---------- Main ----------
def main():
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        raise ValueError("❌ BOT_TOKEN not found in environment variables")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("active", active))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    # Start health server in background
    threading.Thread(target=start_health_server, daemon=True).start()
    logger.info("🚀 Telegram bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
