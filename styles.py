"""Look-and-feel of the bot: watermark styles/fonts/colours, emoji sets, image URLs, inline keyboards, captions."""
import re
from PIL import ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import KIDNAP_ME_URL, UPDATES_CHANNEL
from state import bot_status, game_timers


SHOP_IMAGE_URL = "https://postimg.cc/kR3myZ0S"
LB_IMAGE_URL = "https://i.postimg.cc/P5THW6RQ/file-00000000bce4720b905dc2e04c58fa80.png"
MINE_IMAGE_URL = "https://i.postimg.cc/hjCftW5b/file-0000000079a071fa95971d3b70015fc0.png"
GM_IMAGE_URL = "https://i.postimg.cc/Fs1h0CPs/file-000000001d7872078a894cdf6f6247c9.png"
START_MENU_IMAGE_PAGE = "https://postimg.cc/HrV5wnj0"
WORKFLOW_IMAGE_PAGE = "https://postimg.cc/Q9bk6PD1"
MEDALS = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
SENTIMENT_POSITIVE = ["😊", "😄", "❤️", "🔥", "✨", "🎉", "💖", "😻", "👍"]
SENTIMENT_NEGATIVE = ["😢", "😠", "💔", "😤", "😭", "😞", "😿", "😡", "⚠️"]
SENTIMENT_NEUTRAL = ["🤔", "😐", "👀", "🐾", "🎯", "📌", "💭", "🤷"]


WM_STYLES = {
    "Normal": "normal", "Bold": "bold", "Italic": "italic", "Bold Italic": "bolditalic",
    "Condensed": "condensed", "Light Thin": "light", "Block Strong": "block"
}


VIBGYOR_COLORS = {
    "🟣 Violet": (148, 0, 211, 200), "🔵 Indigo": (75, 0, 130, 200), "🔷 Blue": (0, 0, 255, 200),
    "🟢 Green": (0, 200, 0, 200), "🟡 Yellow": (255, 255, 0, 200), "🟠 Orange": (255, 165, 0, 200),
    "🔴 Red": (255, 0, 0, 200), "⚪ White": (255, 255, 255, 220), "⚫ Black": (0, 0, 0, 220),
}


FONT_PATHS = {
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"],
    "italic": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/freefont/FreeSansOblique.ttf"],
    "bolditalic": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBoldOblique.ttf"],
    "normal": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSans.ttf"],
    "condensed": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"],
    "light": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf"],
    "block": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def load_font(style_key: str, size: int):
    for p in FONT_PATHS.get(style_key, FONT_PATHS["normal"]):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def fancy_title(text: str) -> str:
    """Renders text in Mathematical Bold Script unicode (𝓑𝓮𝓵𝓾𝓰𝓪-style) —
    used as the 'premium font' for titles. Non-letters pass through
    unchanged."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D4D0 + (ord(ch) - ord("A"))))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D4EA + (ord(ch) - ord("a"))))
        else:
            out.append(ch)
    return "".join(out)


def _format_news_body(summary: str) -> str:
    sentences = re.split(r'(?<=[.!?])\s+', summary.strip())
    lines = []
    for s in sentences:
        s = s.strip()
        if len(s) > 20:
            lines.append(s)
        if len(lines) >= 18:
            break
    return "\n".join(lines) if lines else summary[:800]


def _build_color_keyboard(uid: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for label in VIBGYOR_COLORS:
        row.append(InlineKeyboardButton(label, callback_data=f"wm:color:{uid}:{label}", style="primary"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _build_style_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(s, callback_data=f"wm:style:{uid}:{s}", style="primary")] for s in WM_STYLES])


def _build_gm_caption(users: list, date_str: str) -> str:
    display_users = users[-15:] if len(users) > 15 else users
    lines = ["📸 *DAILY ATTENDANCE*\n", "🥱 Mark attendance!\n", f"📅 {date_str}  |  👥 {len(users)}\n", "━━━━━━━━━━━━━━━━━━━━\n"]
    if len(users) > 15:
        lines.append(f"... +{len(users)-15} more...\n")
    for i, user in enumerate(display_users, 1):
        lines.append(f"{i}. {user['name']} • {user['time']}")
    lines += ["\n━━━━━━━━━━━━━━━━━━━━\n", "🔥 +50 pts for check-in"]
    return "\n".join(lines)


TTT_EMPTY, TTT_X, TTT_O = "⬜", "❌", "⭕"


def ttt_build_keyboard(board, disabled=False):
    rows = []
    for row in range(3):
        r = []
        for col in range(3):
            idx = row * 3 + col
            cb = f"ttt:noop:{idx}" if (board[idx] != TTT_EMPTY or disabled) else f"ttt:move:{idx}"
            cell_style = "success" if board[idx] == TTT_O else "primary"
            r.append(InlineKeyboardButton(board[idx], callback_data=cb, style=cell_style))
        rows.append(r)
    return InlineKeyboardMarkup(rows)


def ttt_build_text(g):
    gkey = f"{g['chat_id']}:{g.get('msg_id','')}"
    rem = game_timers.get(gkey, {}).get("remaining", 300)
    tsec = f"{rem//60:02d}:{rem%60:02d}"
    board_str = "\n".join([" ".join(g["board"][r*3+col] for col in range(3)) for r in range(3)])
    status = g.get("status", "playing")
    if status == "playing":
        sl = f"🎯 *{g['x_name'] if g['turn'] == 'X' else g['o_name']}'s turn* {'❌' if g['turn'] == 'X' else '⭕'}  ⏱ `{tsec}`"
    elif status == "timeout":
        sl = f"⏰ *Time up!*\n🏆 *{g.get('winner_name','')}* wins!"
    elif status == "draw":
        sl = "🤝 *Draw!*"
    else:
        sl = f"🏆 *{g.get('winner_name','')}* wins!"
    return f"🎮 *TIC TAC TOE*\n━━━━━━━━━━━━━━\n❌ {g['x_name']}  🆚  ⭕ {g['o_name']}\n━━━━━━━━━━━━━━\n\n{board_str}\n\n━━━━━━━━━━━━━━\n{sl}"


def _mine_setup_keyboard(gkey):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("3 Mines", callback_data=f"mine:set:{gkey}:3", style="success"),
        InlineKeyboardButton("4 Mines", callback_data=f"mine:set:{gkey}:4", style="primary"),
        InlineKeyboardButton("5 Mines", callback_data=f"mine:set:{gkey}:5", style="danger")
    ]])


def _mine_board_keyboard(gkey, state, revealed, disabled=False):
    rows, r = [], []
    for i in range(6):
        is_bomb = state[i]
        if disabled or revealed[i]:
            if is_bomb:
                label, cell_style = "💣", "danger"
            elif revealed[i]:
                label, cell_style = "✅", "success"
            else:
                label, cell_style = "⬜", "primary"
        else:
            label, cell_style = "📦", "danger"
        cb = f"mine:play:{gkey}:{i}" if not disabled and not revealed[i] else f"mine:noop:{gkey}:{i}"
        r.append(InlineKeyboardButton(label, callback_data=cb, style=cell_style))
        if len(r) == 3:
            rows.append(r); r = []
    if r:
        rows.append(r)
    return InlineKeyboardMarkup(rows)


def mine_build_text(g, rem):
    bombs, total_safe, opened = g["bombs"], 6 - g["bombs"], sum(1 for x in g["revealed"] if x)
    if g.get("status") == "timeout": return "⏰ *Time Up!*\n\nLost *-5 pts*."
    elif g.get("status") == "lost": return "💥 *BOOM!*\n\nLost *-5 pts*."
    elif g.get("status") == "won": return f"🎉 *YOU WIN!*\n\nAll {total_safe} safe boxes found! Won *+700 pts*."
    else: return f"💣 *MINESWEEPER*\nMines: {bombs}  |  Safe: {opened}/{total_safe}\n⏱ Time: `{rem}s`"


def _start_main_menu_kb() -> InlineKeyboardMarkup:
    bot_username = bot_status.get("username", "")
    kidnap_url = f"https://t.me/{bot_username}?startgroup=true&admin=post_messages+edit_messages+delete_messages+restrict_members+invite_users+pin_messages" if bot_username else KIDNAP_ME_URL
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🫦 KIDNAP ME 🫦", url=kidnap_url, style="danger")],
        [InlineKeyboardButton("💖 UPDATES CHANNEL 💖", url=UPDATES_CHANNEL, style="primary")],
        [InlineKeyboardButton("😼 WHAT I CAN DO ?", callback_data="menu:whatido", style="success")],
        [InlineKeyboardButton("👀 WORKFLOW", callback_data="menu:workflow", style="danger")],
    ])


def _back_only_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("BACK 🫤", callback_data="menu:back", style="danger")]])
