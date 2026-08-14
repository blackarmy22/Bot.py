"""
Telegram Video/Audio Downloader Bot
====================================
Downloads video/audio from YouTube, TikTok, Instagram, Facebook, X,
Reddit, Pinterest, Vimeo, Dailymotion, SoundCloud, Twitch, Snapchat,
Threads, Likee, Kuaishou, Bilibili, Twitch Clips, etc. using yt-dlp.
"""

import asyncio
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from flask import Flask
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from yt_dlp import YoutubeDL


# =========================================================
# CONFIG
# =========================================================

# تۆکنی بۆتەکەت لێرەدا جێگیرکراوە
BOT_TOKEN = "8909513843:AAEJFY8PlfBL20-kU_-n4fYiLgrl_KAJ1j0"

PORT = int(os.getenv("PORT", "10000"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "48"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOW_PLAYLISTS = os.getenv("ALLOW_PLAYLISTS", "0").strip() == "1"

# گۆڕینی ڕێڕەوی کاتی بۆ ناو فۆڵدەری کارکردن بۆ ئەوەی لە تێرمۆکس کێشەی پرمشن دروست نەبێت
TMP_DIR = Path(os.getenv("TMP_DIR", "./telegram_downloader"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

COOKIE_FILE = Path(os.getenv("COOKIE_FILE", "cookies.txt"))

YOUTUBE_PLAYER_CLIENTS = ["android", "ios", "web"]


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("telegram-downloader")


# =========================================================
# FLASK HEALTH CHECK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Telegram Downloader Bot is running ✅"


@app.route("/health")
def health():
    return {"status": "ok"}


def start_health_server():
    def run_flask():
        app.run(host="0.0.0.0", port=PORT, threaded=True)

    Thread(target=run_flask, daemon=True).start()


# =========================================================
# TELEGRAM SETUP
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

user_urls: dict[int, str] = {}


# =========================================================
# URL HELPERS
# =========================================================

URL_REGEX = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)

PLATFORM_DOMAINS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "music.youtube.com": "YouTube Music",
    "tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "x.com": "X",
    "twitter.com": "X",
    "reddit.com": "Reddit",
    "pinterest.com": "Pinterest",
    "vimeo.com": "Vimeo",
    "dailymotion.com": "Dailymotion",
    "soundcloud.com": "SoundCloud",
    "twitch.tv": "Twitch",
    "clips.twitch.tv": "Twitch Clips",
    "snapchat.com": "Snapchat",
    "threads.net": "Threads",
    "likee.video": "Likee",
    "kuaishou.com": "Kuaishou",
    "bilibili.com": "Bilibili",
    "vk.com": "VK",
    "linkedin.com": "LinkedIn",
    "streamable.com": "Streamable",
    "bandcamp.com": "Bandcamp",
    "mixcloud.com": "Mixcloud",
}


def extract_url(text: str | None) -> str | None:
    if not text:
        return None
    match = URL_REGEX.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,!?)]}>\"'")


def get_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def detect_platform(url: str) -> str:
    domain = get_domain(url)
    for domain_name, platform in PLATFORM_DOMAINS.items():
        if domain == domain_name or domain.endswith("." + domain_name):
            return platform
    return "Auto"


def is_youtube(url: str) -> bool:
    return detect_platform(url) == "YouTube"


# =========================================================
# KEYBOARD
# =========================================================

def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎬 MP4", callback_data="mode_mp4"),
                InlineKeyboardButton(text="🎵 MP3", callback_data="mode_mp3"),
            ],
            [InlineKeyboardButton(text="📦 MP4 + MP3", callback_data="mode_both")],
        ]
    )


# =========================================================
# COMMAND HANDLERS
# =========================================================

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer(
        "سڵاو 👋\n\n"
        "🎬 بۆتی داونلۆدکردنی ڤیدیۆ و دەنگ.\n\n"
        "لینکی ڤیدیۆکەت بنێرە.\n\n"
        "دواتر هەڵبژێرە:\n"
        "🎬 MP4\n"
        "🎵 MP3\n"
        "📦 هەردووکیان\n\n"
        "TikTok Photo Mode ـیش پشتگیری دەکرێت "
        "ئەگەر extractor ـەکە media ـەکانی پۆستەکە بدات."
    )


@dp.message(Command("help"))
async def help_handler(message: types.Message):
    await message.answer(
        "📖 ڕێنمایی\n\n"
        "1️⃣ لینکەکە بنێرە.\n"
        "2️⃣ MP4، MP3 یان هەردووکیان هەڵبژێرە.\n"
        "3️⃣ چاوەڕێ بکە تا فایلەکان دابەزێنرێن.\n\n"
        "⚠️ ناوەڕۆکی private یان login-required "
        "لەوانەیە cookies پێویست بکات.\n"
        "⚠️ یوتیوب هەندێک جار داوای پشتڕاستکردنەوە دەکات "
        "کە بۆتی ئۆتۆماتیک نییت؛ ئەگەر ئەمە ڕوویدا، "
        "پێویستە فایلی cookies.txt زیاد بکرێت.\n"
        f"⚠️ زۆرترین قەبارەی فایل: {MAX_FILE_SIZE_MB}MB "
        "(سنووری ڕاستەقینەی Telegram Bot API نزیکەی 50MB ـە)."
    )


@dp.message(F.text)
async def url_handler(message: types.Message):
    url = extract_url(message.text)
    if not url:
        await message.answer("❌ تکایە لینکێکی ڕاست بنێرە.")
        return

    user_urls[message.from_user.id] = url
    platform = detect_platform(url)

    await message.answer(
        f"🔗 پلاتفۆرم: {platform}\n\nچۆن دەتەوێت دایبەزێنم؟",
        reply_markup=download_keyboard(),
    )


# =========================================================
# FILENAME / COOKIES HELPERS
# =========================================================

def safe_filename(name) -> str:
    name = str(name or "download")
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return (name or "download")[:150]


def cookies_available() -> bool:
    try:
        return COOKIE_FILE.exists() and COOKIE_FILE.is_file() and COOKIE_FILE.stat().st_size > 0
    except Exception:
        return False


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# =========================================================
# YT-DLP OPTIONS
# =========================================================

def base_options(workdir: Path, url: str = "") -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "noplaylist": not ALLOW_PLAYLISTS,
        "ignoreerrors": False,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "sleep_interval_requests": 1,
        "outtmpl": str(workdir / "%(title).150B [%(id)s].%(ext)s"),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    }

    if cookies_available():
        options["cookiefile"] = str(COOKIE_FILE)

    if is_youtube(url):
        options["extractor_args"] = {
            "youtube": {"player_client": YOUTUBE_PLAYER_CLIENTS}
        }

    return options


def download_video_sync(url: str, workdir: Path):
    options = base_options(workdir, url)
    options.update(
        {
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/best"
            ),
            "merge_output_format": "mp4",
        }
    )
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=True)


def download_audio_sync(url: str, workdir: Path):
    options = base_options(workdir, url)
    options.update(
        {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    )
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=True)


# =========================================================
# FILE HELPERS
# =========================================================

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".opus", ".wav", ".flac"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def find_files(workdir: Path, extensions: set[str]) -> list[Path]:
    result = [f for f in workdir.rglob("*") if f.is_file() and f.suffix.lower() in extensions]
    return sorted(result, key=lambda f: f.stat().st_mtime)


def valid_size(file: Path) -> bool:
    try:
        return file.stat().st_size <= MAX_FILE_SIZE_BYTES
    except Exception:
        return False


def get_title(info) -> str:
    if isinstance(info, dict) and info.get("title"):
        return safe_filename(info["title"])
    return "download"


# =========================================================
# SEND HELPERS
# =========================================================

async def send_mp4(message, file: Path, title: str) -> bool:
    if not valid_size(file):
        await message.answer(f"❌ فایل MP4 زۆر گەورەیە (سنوور: {MAX_FILE_SIZE_MB}MB).")
        return False
    await message.answer_document(document=FSInputFile(file), caption=f"🎬 {title}")
    return True


async def send_mp3(message, file: Path, title: str) -> bool:
    if not valid_size(file):
        await message.answer("❌ فایل MP3 زۆر گەورەیە.")
        return False
    await message.answer_audio(audio=FSInputFile(file), title=title, caption=f"🎵 {title}")
    return True


async def send_images(message, images: list[Path]) -> int:
    count = 0
    for image in images:
        if not valid_size(image):
            continue
        try:
            await message.answer_photo(photo=FSInputFile(image))
            count += 1
        except Exception as exc:
            logger.warning("Image send failed: %s", exc)
    return count


# =========================================================
# DOWNLOAD PIPELINES
# =========================================================

async def process_video_only(message, url: str, workdir: Path):
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, download_video_sync, url, workdir)
    title = get_title(info)

    videos = find_files(workdir, VIDEO_EXTENSIONS)
    if videos:
        await send_mp4(message, videos[-1], title)
        return

    images = find_files(workdir, IMAGE_EXTENSIONS)
    if images:
        if await send_images(message, images) == 0:
            raise RuntimeError("No images could be sent.")
        return

    raise RuntimeError("No media file was created.")


async def process_audio_only(message, url: str, workdir: Path):
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed on the server.")

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, download_audio_sync, url, workdir)
    title = get_title(info)

    audios = find_files(workdir, AUDIO_EXTENSIONS)
    if not audios:
        raise RuntimeError("MP3 file was not created.")
    await send_mp3(message, audios[-1], title)


async def process_both(message, url: str, workdir: Path):
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg is not installed on the server.")

    loop = asyncio.get_running_loop()

    info = await loop.run_in_executor(None, download_video_sync, url, workdir)
    title = get_title(info)

    video_files = find_files(workdir, VIDEO_EXTENSIONS)
    if not video_files:
        raise RuntimeError("MP4 file was not created.")
    video_file = video_files[-1]

    await send_mp4(message, video_file, title)

    audio_workdir = workdir / "audio"
    audio_workdir.mkdir(exist_ok=True)
    mp3_file = audio_workdir / f"{title}.mp3"

    command = [
        "ffmpeg", "-y",
        "-i", str(video_file),
        "-vn",
        "-codec:a", "libmp3lame",
        "-b:a", "192k",
        str(mp3_file),
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="ignore")
        raise RuntimeError("FFmpeg MP3 conversion failed: " + error[-500:])

    if not mp3_file.exists():
        raise RuntimeError("MP3 file was not created.")

    await send_mp3(message, mp3_file, title)


MODE_HANDLERS = {
    "mp4": process_video_only,
    "mp3": process_audio_only,
    "both": process_both,
}


# =========================================================
# ORCHESTRATION
# =========================================================

async def process_download(message, url: str, mode: str):
    async with download_semaphore:
        workdir = Path(tempfile.mkdtemp(prefix="job_", dir=TMP_DIR))
        status = None

        try:
            platform = detect_platform(url)
            status = await message.answer(
                f"⏳ {platform} ـەکە خەریکە دابەزێت...\n\nتکایە چاوەڕێ بکە."
            )

            handler = MODE_HANDLERS.get(mode)
            if handler is None:
                raise RuntimeError("Unknown download mode.")

            await handler(message, url, workdir)

            if status:
                try:
                    await status.delete()
                except Exception:
                    pass

        except Exception as exc:
            logger.exception("Download error: %s", url)

            if status:
                try:
                    await status.delete()
                except Exception:
                    pass

            error_text = str(exc)
            if len(error_text) > 700:
                error_text = error_text[-700:]

            hints = [
                "• لینکەکە هەڵەیە",
                "• ناوەڕۆکەکە private ـە",
                "• login/cookies پێویستە",
                "• پلاتفۆرمەکە download ـەکەی سنووردار کردووە",
                "• فایلەکە زۆر گەورەیە",
            ]
            if "Sign in to confirm" in error_text or "not a bot" in error_text:
                hints.insert(
                    0,
                    "• یوتیوب داوای پشتڕاستکردنەوەی مرۆڤ دەکات — "
                    "پێویستە cookies.txt زیاد بکرێت",
                )
            if "ffmpeg is not installed" in error_text:
                hints.insert(0, "• ffmpeg لەسەر سێرڤەرەکە دامەزراو نییە")

            await message.answer(
                "❌ دابەزاندن سەرکەوتوو نەبوو.\n\n"
                "هۆکارە باوەکان:\n" + "\n".join(hints) + f"\n\n🔎 {error_text}"
            )

        finally:
            shutil.rmtree(workdir, ignore_errors=True)


# =========================================================
# CALLBACK HANDLERS
# =========================================================

async def _handle_mode_callback(callback: types.CallbackQuery, mode: str, ack_text: str):
    url = user_urls.get(callback.from_user.id)
    if not url:
        await callback.answer("سەرەتا لینکێک بنێرە.", show_alert=True)
        return

    await callback.answer(ack_text)
    asyncio.create_task(process_download(callback.message, url, mode))


@dp.callback_query(F.data == "mode_mp4")
async def mp4_callback(callback: types.CallbackQuery):
    await _handle_mode_callback(callback, "mp4", "دەستم پێکرد بە MP4...")


@dp.callback_query(F.data == "mode_mp3")
async def mp3_callback(callback: types.CallbackQuery):
    await _handle_mode_callback(callback, "mp3", "دەستم پێکرد بە MP3...")


@dp.callback_query(F.data == "mode_both")
async def both_callback(callback: types.CallbackQuery):
    await _handle_mode_callback(callback, "both", "MP4 + MP3 دەست پێدەکات...")


# =========================================================
# MAIN
# =========================================================

async def run_bot_forever():
    backoff = 5
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Polling crashed, restarting in %s seconds...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def main():
    start_health_server()
    logger.info("Starting Telegram bot...")

    if not cookies_available():
        logger.warning(
            "No cookies file found at %s — YouTube downloads may fail with "
            "'Sign in to confirm you're not a bot'.",
            COOKIE_FILE,
        )

    if not ffmpeg_available():
        logger.warning(
            "ffmpeg was not found on PATH — MP3 conversion will fail. "
            "Install it with: pkg install ffmpeg"
        )

    await run_bot_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
