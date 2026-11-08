import html
import logging
import os
import subprocess

import telebot
from dotenv import load_dotenv

from mcserver import TMUX_SESSION, get_mc_version, get_online_players, get_server_uptime, get_tps
from pisystem import (
    THROTTLED_FLAGS,
    format_optional,
    format_uptime,
    get_local_ip,
    get_pi_stats,
    get_public_ip,
    get_system_uptime,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [pid=%(process)d] %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN not found. Add BOT_TOKEN=... to .env")

bot = telebot.TeleBot(TOKEN)


def setup_update_dedup():
    """Drop duplicate updates for the same message.

    Telegram occasionally registers one message as two updates with different
    update_id (observed with VPN/proxy retransmissions). Without filtering the
    bot would answer such a message twice.
    """
    orig_get_updates = bot.get_updates

    def get_updates_dedup(*args, **kwargs):
        updates = orig_get_updates(*args, **kwargs)
        if not updates:
            return updates
        # Confirm every update, including dropped duplicates
        bot.last_update_id = max(u.update_id for u in updates)
        seen = set()
        unique = []
        for update in updates:
            if update.message:
                key = (update.message.chat.id, update.message.message_id)
                if key in seen:
                    logger.warning("Duplicate update %s for message %s dropped", update.update_id, key)
                    continue
                seen.add(key)
            unique.append(update)
        return unique

    bot.get_updates = get_updates_dedup
    logger.info("Update deduplication enabled")


def reply_logged(message, text):
    """Reply to a message with HTML formatting and log any sending errors."""
    try:
        bot.reply_to(message, text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to send reply")


# ----------------------------- Server replies -----------------------------


def tps_status_indicator(tps):
    """Return an emoji showing how healthy the TPS value is."""
    if tps >= 19.5:
        return "🟢"
    if tps >= 15.0:
        return "🟡"
    return "🔴"


def tmux_error_reply():
    """Return a message explaining why a tmux command failed."""
    try:
        subprocess.run(["tmux", "list-sessions"], check=True, capture_output=True)
    except FileNotFoundError:
        return "❌ Ошибка: не удалось запустить tmux. Установлен ли tmux?"
    except subprocess.CalledProcessError:
        return "❌ Ошибка: в tmux нет запущенных сессий. Запущена ли сессия сервера?"
    return f"❌ Ошибка: не удалось обратиться к tmux-сессии '{TMUX_SESSION}'."


def get_tps_reply():
    try:
        result = get_tps()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return tmux_error_reply()
    if result is None:
        return "⚠️ Не удалось прочитать TPS. Поддерживает ли сервер команду 'tps' (Paper/Spigot)?"
    tps_1m, tps_5m, tps_15m = result
    return (
        f"{tps_status_indicator(tps_1m)} ⚡ TPS (1м / 5м / 15м): "
        f"<b>{tps_1m:.1f} / {tps_5m:.1f} / {tps_15m:.1f}</b>"
    )


def get_online_reply():
    try:
        result = get_online_players()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return tmux_error_reply()
    if result is None:
        return f"⚠️ Не удалось прочитать вывод сервера. Запущен ли он в tmux-сессии '{TMUX_SESSION}'?"
    online, max_players, names = result
    if online == 0:
        return f"😴 <b>0/{max_players}</b> игроков онлайн."
    return f"👥 <b>{online}/{max_players}</b> игроков онлайн: {html.escape(names)}"


def get_uptime_reply():
    server_uptime = get_server_uptime()
    if server_uptime is None:
        return "🛑 Minecraft-сервер не запущен (java-процесс не найден)."
    system_uptime = format_uptime(get_system_uptime()) if get_system_uptime() else "N/A"
    return (
        "⏱️ <b>Аптайм</b>\n"
        f"🎮 Minecraft-сервер: <b>{format_uptime(server_uptime)}</b>\n"
        f"🍓 Система: <b>{system_uptime}</b>"
    )


def get_server_info_reply():
    version = get_mc_version()
    version_text = version if version else "unknown"
    local_ip = get_local_ip()
    public_ip = get_public_ip()
    return (
        "🎮 <b>Minecraft-сервер</b>\n"
        f"📦 Версия: <code>{html.escape(version_text)}</code>\n"
        f"🏠 Локальный IP: <code>{local_ip or 'N/A'}</code>\n"
        f"🌍 Публичный IP: <code>{public_ip or 'N/A'}</code>"
    )


# ------------------------------- Pi replies -------------------------------


def get_pi_reply():
    try:
        stats = get_pi_stats()
    except Exception:
        logger.exception("Failed to collect Pi stats")
        return "❌ Ошибка: не удалось собрать статистику Raspberry Pi. Проверьте логи бота."
    load = format_optional(
        stats["load_1"] is not None,
        f"{stats['load_1']:.2f} / {stats['load_5']:.2f} / {stats['load_15']:.2f}",
    )
    if stats["total_kb"]:
        ram = (
            f"{stats['used_kb'] / 1024:.1f} MB / {stats['total_kb'] / 1024:.1f} MB"
            f" ({stats['mem_percent']:.1f}%)"
        )
    else:
        ram = "N/A"
    temp = (
        f"{stats['temp_c']:.1f}°C" if stats["temp_c"] is not None else "N/A"
    )
    if stats["throttled"] is None:
        throttling = "N/A"
    elif stats["throttled"] == 0:
        throttling = "✅ OK"
    else:
        flags = [text for mask, text in THROTTLED_FLAGS if stats["throttled"] & mask]
        throttling = "⚠️ " + "; ".join(flags)
    uptime = format_optional(
        stats["uptime_seconds"] is not None,
        format_uptime(stats["uptime_seconds"]),
    )
    return (
        "🍓 <b>Raspberry Pi</b>\n"
        f"⚙️ Нагрузка CPU (1м/5м/15м): <code>{load}</code>\n"
        f"🧠 ОЗУ: <code>{ram}</code>\n"
        f"🌡️ Температура: <code>{temp}</code>\n"
        f"🔌 Троттлинг: <code>{throttling}</code>\n"
        f"⏱️ Аптайм: <code>{uptime}</code>"
    )


# ------------------------------- Bot handlers ------------------------------


BOT_COMMANDS = [
    ("tps", "Показать TPS сервера"),
    ("online", "Показать игроков онлайн"),
    ("uptime", "Аптайм сервера и системы"),
    ("about", "Информация о сервере"),
    ("raspberrypi", "Статистика Raspberry Pi"),
]


@bot.message_handler(commands=["start"])
def start(message):
    lines = [f"👋 Привет, {message.from_user.first_name}!", "", "Доступные команды:"]
    lines += [f"/{command} — {description}" for command, description in BOT_COMMANDS]
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["tps"])
def tps_command(message):
    reply_logged(message, get_tps_reply())


@bot.message_handler(commands=["uptime"])
def uptime_command(message):
    reply_logged(message, get_uptime_reply())


@bot.message_handler(commands=["about"])
def about_command(message):
    reply_logged(message, get_server_info_reply())


@bot.message_handler(commands=["raspberrypi"])
def raspberrypi_command(message):
    reply_logged(message, get_pi_reply())


@bot.message_handler(commands=["online"])
def online_command(message):
    reply_logged(message, get_online_reply())


if __name__ == "__main__":
    bot.set_my_commands([
        telebot.types.BotCommand("start", "Start the bot"),
        *[telebot.types.BotCommand(command, description) for command, description in BOT_COMMANDS],
    ])
    print("Bot is running")
    setup_update_dedup()
    # Drop the update backlog so the bot does not re-answer old messages after a restart
    bot.delete_webhook(drop_pending_updates=True)
    bot.infinity_polling()
