"""Общие утилиты, используемые в нескольких модулях."""
import shutil


def _has_ffmpeg() -> bool:
    """Возвращает True если ffmpeg доступен в системном PATH."""
    return shutil.which("ffmpeg") is not None


def _has_ytdlp() -> bool:
    """Возвращает True если yt-dlp доступен в системном PATH."""
    return shutil.which("yt-dlp") is not None
