import asyncio
import logging
import os
import re
import shutil
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing. "
        "Add BOT_TOKEN in Render Environment Variables."
    )

PORT = int(os.getenv("PORT", "10000"))

MAX_CONCURRENT_DOWNLOADS = int(
    os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")
)

MAX_FILE_SIZE_MB = int(
    os.getenv("MAX_FILE_SIZE_MB", "48")
)

MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

ALLOW_PLAYLISTS = (
    os.getenv("ALLOW_PLAYLISTS", "0").strip() == "1"
)

TMP_DIR = Path(
    os.getenv("TMP_DIR", "./telegram_downloader")
)

TMP_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# YOUTUBE
# =========================================================

# Cookies intentionally NOT used.
#
# These clients may work for some public YouTube videos,
# but YouTube can still require verification depending
# on the request/IP/video.
YOUTUBE_PLAYER_CLIENTS = [
    "android_vr",
    "web_safari",
]


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
        app.run(
            host="0.0.0.0",
            port=PORT,
            threaded=True,
        )

    Thread(
        target=run_flask,
        daemon=True,
    ).start()


# =========================================================
# TELEGRAM SETUP
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

download_semaphore = asyncio.Semaphore(
    MAX_CONCURRENT_DOWNLOADS
)

user_urls: dict[int, str] = {}


# =========================================================
# URL HELPERS
# =========================================================

URL_REGEX = re.compile(
    r"""https?://[^\s<>"']+""",
    re.IGNORECASE,
)


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

    return match.group(0).rstrip(
        ".,!?)]}>\"'"
    )


def get_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        return host

    except Exception:
        return ""


def detect_platform(url: str) -> str:
    domain = get_domain(url)

    for domain_name, platform in PLATFORM_DOMAINS.items():
        if (
            domain == domain_name
            or domain.endswith("." + domain_name)
        ):
            return platform

    return "Auto"


def is_youtube(url: str) -> bool:
    return detect_platform(url) in {
        "YouTube",
        "YouTube Music",
    }


# =========================================================
# KEYBOARD
# =========================================================

def download_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 MP4",
                    callback_data="mode_mp4",
                ),
                InlineKeyboardButton(
                    text="🎵 MP3",
                    callback_data="mode_mp3",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦 MP4 + MP3",
                    callback_data="mode_both",
                )
            ],
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
        "⚠️ YouTube هەندێک جار verification "
        "داوا دەکات، بەتایبەتی لە hosting IP ـەکان."
    )


@dp.message(Command("help"))
async def help_handler(message: types.Message):
    await message.answer(
        "📖 ڕێنمایی\n\n"
        "1️⃣ لینکەکە بنێرە.\n"
        "2️⃣ MP4، MP3 یان هەردووکیان هەڵبژێرە.\n"
        "3️⃣ چاوەڕێ بکە تا فایلەکە دابەزێت.\n\n"
        "⚠️ ناوەڕۆکی private/login-required "
        "لەوانەیە کار نەکات.\n\n"
        "⚠️ YouTube لە هەندێک IP دا "
        "داوای verification دەکات.\n\n"
        f"⚠️ زۆرترین قەبارە: "
        f"{MAX_FILE_SIZE_MB}MB"
    )


@dp.message(F.text)
async def url_handler(message: types.Message):
    url = extract_url(message.text)

    if not url:
        await message.answer(
            "❌ تکایە لینکێکی ڕاست بنێرە."
        )
        return

    user_id = message.from_user.id

    user_urls[user_id] = url

    platform = detect_platform(url)

    await message.answer(
        f"🔗 پلاتفۆرم: {platform}\n\n"
        "چۆن دەتەوێت دایبەزێنم؟",
        reply_markup=download_keyboard(),
    )


# =========================================================
# FILE HELPERS
# =========================================================

def safe_filename(name) -> str:
    name = str(name or "download")

    name = re.sub(
        r'[\/:*?"<>|]+',
        "_",
        name,
    )

    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip()

    return (name or "download")[:150]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def valid_size(file: Path) -> bool:
    try:
        return (
            file.stat().st_size
            <= MAX_FILE_SIZE_BYTES
        )
    except Exception:
        return False


def get_title(info) -> str:
    if (
        isinstance(info, dict)
        and info.get("title")
    ):
        return safe_filename(info["title"])

    return "download"


# =========================================================
# YT-DLP OPTIONS
# =========================================================

def base_options(
    workdir: Path,
    url: str = "",
) -> dict:

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

        "outtmpl": str(
            workdir
            / "%(title).150B [%(id)s].%(ext)s"
        ),

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) "
                "Version/15.5 Safari/605.1.15"
            ),
            "Accept-Language": (
                "en-US,en;q=0.9"
            ),
        },
    }

    # -----------------------------------------------------
    # YouTube
    # -----------------------------------------------------

    if is_youtube(url):
        options["extractor_args"] = {
            "youtube": {
                "player_client":
                    YOUTUBE_PLAYER_CLIENTS,
            }
        }

    # -----------------------------------------------------
    # IMPORTANT
    # -----------------------------------------------------
    # No cookies are loaded here.
    #
    # This bot intentionally works without
    # cookies.txt.
    # -----------------------------------------------------

    return options


# =========================================================
# DOWNLOAD FUNCTIONS
# =========================================================

def download_video_sync(
    url: str,
    workdir: Path,
):

    options = base_options(
        workdir,
        url,
    )

    options.update(
        {
            "format": (
                "bestvideo[ext=mp4]+"
                "bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/"
                "best"
            ),
            "merge_output_format": "mp4",
        }
    )

    with YoutubeDL(options) as ydl:
        return ydl.extract_info(
            url,
            download=True,
        )


def download_audio_sync(
    url: str,
    workdir: Path,
):

    options = base_options(
        workdir,
        url,
    )

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
        return ydl.extract_info(
            url,
            download=True,
        )


# =========================================================
# FILE SEARCH
# =========================================================

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".m4v",
    ".avi",
}

AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".aac",
    ".opus",
    ".wav",
    ".flac",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def find_files(
    workdir: Path,
    extensions: set[str],
) -> list[Path]:

    result = [
        f
        for f in workdir.rglob("*")
        if (
            f.is_file()
            and f.suffix.lower() in extensions
        )
    ]

    return sorted(
        result,
        key=lambda f: f.stat().st_mtime,
    )


# =========================================================
# TELEGRAM SEND HELPERS
# =========================================================

async def send_mp4(
    message,
    file: Path,
    title: str,
) -> bool:

    if not valid_size(file):
        await message.answer(
            f"❌ فایل MP4 زۆر گەورەیە.\n"
            f"سنوور: {MAX_FILE_SIZE_MB}MB"
        )
        return False

    await message.answer_document(
        document=FSInputFile(file),
        caption=f"🎬 {title}",
    )

    return True


async def send_mp3(
    message,
    file: Path,
    title: str,
) -> bool:

    if not valid_size(file):
        await message.answer(
            f"❌ فایل MP3 زۆر گەورەیە.\n"
            f"سنوور: {MAX_FILE_SIZE_MB}MB"
        )
        return False

    await message.answer_audio(
        audio=FSInputFile(file),
        title=title,
        caption=f"🎵 {title}",
    )

    return True


async def send_images(
    message,
    images: list[Path],
) -> int:

    count = 0

    for image in images:

        if not valid_size(image):
            continue

        try:
            await message.answer_photo(
                photo=FSInputFile(image)
            )

            count += 1

        except Exception as exc:
            logger.warning(
                "Image send failed: %s",
                exc,
            )

    return count


# =========================================================
# DOWNLOAD PIPELINES
# =========================================================

async def process_video_only(
    message,
    url: str,
    workdir: Path,
):

    loop = asyncio.get_running_loop()

    info = await loop.run_in_executor(
        None,
        download_video_sync,
        url,
        workdir,
    )

    title = get_title(info)

    videos = find_files(
        workdir,
        VIDEO_EXTENSIONS,
    )

    if videos:
        await send_mp4(
            message,
            videos[-1],
            title,
        )
        return

    images = find_files(
        workdir,
        IMAGE_EXTENSIONS,
    )

    if images:
        if (
            await send_images(
                message,
                images,
            )
            == 0
        ):
            raise RuntimeError(
                "No images could be sent."
            )

        return

    raise RuntimeError(
        "No media file was created."
    )


async def process_audio_only(
    message,
    url: str,
    workdir: Path,
):

    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is not installed on the server."
        )

    loop = asyncio.get_running_loop()

    info = await loop.run_in_executor(
        None,
        download_audio_sync,
        url,
        workdir,
    )

    title = get_title(info)

    audios = find_files(
        workdir,
        AUDIO_EXTENSIONS,
    )

    if not audios:
        raise RuntimeError(
            "MP3 file was not created."
        )

    await send_mp3(
        message,
        audios[-1],
        title,
    )


async def process_both(
    message,
    url: str,
    workdir: Path,
):

    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is not installed on the server."
        )

    loop = asyncio.get_running_loop()

    info = await loop.run_in_executor(
        None,
        download_video_sync,
        url,
        workdir,
    )

    title = get_title(info)

    video_files = find_files(
        workdir,
        VIDEO_EXTENSIONS,
    )

    if not video_files:
        raise RuntimeError(
            "MP4 file was not created."
        )

    video_file = video_files[-1]

    await send_mp4(
        message,
        video_file,
        title,
    )

    audio_workdir = workdir / "audio"

    audio_workdir.mkdir(
        exist_ok=True
    )

    mp3_file = (
        audio_workdir
        / f"{title}.mp3"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_file),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(mp3_file),
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await process.communicate()

    if process.returncode != 0:

        error = stderr.decode(
            "utf-8",
            errors="ignore",
        )

        raise RuntimeError(
            "FFmpeg MP3 conversion failed: "
            + error[-500:]
        )

    if not mp3_file.exists():
        raise RuntimeError(
            "MP3 file was not created."
        )

    await send_mp3(
        message,
        mp3_file,
        title,
    )


MODE_HANDLERS = {
    "mp4": process_video_only,
    "mp3": process_audio_only,
    "both": process_both,
}


# =========================================================
# DOWNLOAD ORCHESTRATION
# =========================================================

async def process_download(
    message,
    url: str,
    mode: str,
):

    async with download_semaphore:

        workdir = Path(
            tempfile.mkdtemp(
                prefix="job_",
                dir=TMP_DIR,
            )
        )

        status = None

        try:

            platform = detect_platform(url)

            status = await message.answer(
                f"⏳ {platform} ـەکە خەریکە "
                f"دابەزێت...\n\n"
                "تکایە چاوەڕێ بکە."
            )

            handler = MODE_HANDLERS.get(mode)

            if handler is None:
                raise RuntimeError(
                    "Unknown download mode."
                )

            await handler(
                message,
                url,
                workdir,
            )

            if status:

                try:
                    await status.delete()

                except Exception:
                    pass

        except Exception as exc:

            logger.exception(
                "Download error: %s",
                url,
            )

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
                "• login پێویستە",
                "• پلاتفۆرمەکە download ـەکەی "
                "سنووردار کردووە",
                "• فایلەکە زۆر گەورەیە",
            ]

            lower_error = error_text.lower()

            if (
                "sign in to confirm"
                in lower_error
                or "not a bot"
                in lower_error
                or "confirm you're not a bot"
                in lower_error
            ):
                hints.insert(
                    0,
                    "• YouTube داوای verification "
                    "دەکات. بێ cookies هیچ "
                    "ضمانەتێکی نییە کە Render "
                    "بتوانێت ئەمە چارەسەر بکات.",
                )

            if "403" in lower_error:
                hints.insert(
                    0,
                    "• YouTube داوای access ـی "
                    "زیاتر کردووە (HTTP 403).",
                )

            if "ffmpeg is not installed" in lower_error:
                hints.insert(
                    0,
                    "• ffmpeg لەسەر سێرڤەرەکە "
                    "دامەزراو نییە.",
                )

            await message.answer(
                "❌ دابەزاندن سەرکەوتوو نەبوو.\n\n"
                "هۆکارە باوەکان:\n"
                + "\n".join(hints)
                + f"\n\n🔎 {error_text}"
            )

        finally:

            shutil.rmtree(
                workdir,
                ignore_errors=True,
            )


# =========================================================
# CALLBACK HANDLERS
# =========================================================

async def _handle_mode_callback(
    callback: types.CallbackQuery,
    mode: str,
    ack_text: str,
):

    url = user_urls.get(
        callback.from_user.id
    )

    if not url:

        await callback.answer(
            "سەرەتا لینکێک بنێرە.",
            show_alert=True,
        )

        return

    await callback.answer(
        ack_text
    )

    asyncio.create_task(
        process_download(
            callback.message,
            url,
            mode,
        )
    )


@dp.callback_query(F.data == "mode_mp4")
async def mp4_callback(
    callback: types.CallbackQuery,
):

    await _handle_mode_callback(
        callback,
        "mp4",
        "دەستم پێکرد بە MP4...",
    )


@dp.callback_query(F.data == "mode_mp3")
async def mp3_callback(
    callback: types.CallbackQuery,
):

    await _handle_mode_callback(
        callback,
        "mp3",
        "دەستم پێکرد بە MP3...",
    )


@dp.callback_query(F.data == "mode_both")
async def both_callback(
    callback: types.CallbackQuery,
):

    await _handle_mode_callback(
        callback,
        "both",
        "MP4 + MP3 دەست پێدەکات...",
    )


# =========================================================
# BOT LOOP
# =========================================================

async def run_bot_forever():

    backoff = 5

    while True:

        try:

            await bot.delete_webhook(
                drop_pending_updates=True
            )

            await dp.start_polling(
                bot,
                allowed_updates=(
                    dp.resolve_used_update_types()
                ),
            )

            break

        except asyncio.CancelledError:
            raise

        except Exception:

            logger.exception(
                "Polling crashed, "
                "restarting in %s seconds...",
                backoff,
            )

            await asyncio.sleep(
                backoff
            )

            backoff = min(
                backoff * 2,
                60,
            )


# =========================================================
# MAIN
# =========================================================

async def main():

    start_health_server()

    logger.info(
        "Starting Telegram downloader bot..."
    )

    if not ffmpeg_available():

        logger.warning(
            "ffmpeg was not found on PATH. "
            "MP3 conversion will fail."
        )

    logger.info(
        "Cookies are disabled."
    )

    logger.info(
        "YouTube clients: %s",
        YOUTUBE_PLAYER_CLIENTS,
    )

    await run_bot_forever()


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped."
        )
