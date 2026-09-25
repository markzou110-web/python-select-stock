"""
Configuration management for Alpha Vision backend.
Loads settings from environment variables with fallback to defaults.
"""
import os
from typing import Optional, Dict
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if exists
load_dotenv()

import platform


DEFAULT_SENTINEL_SCHEDULE_TIMES = "09:30,10:00,10:30,11:00,13:00,13:30,14:00,14:30"

# Base directory
BASE_DIR = Path(__file__).parent.parent

# macOS Fork Safety Fix
if platform.system() == "Darwin":
    os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"


class Config:
    """Application configuration with environment variable support."""

    # Bark Push Notification
    BARK_KEY: str = os.getenv("BARK_KEY", "")
    BARK_URL_TEMPLATE: str = "https://api.day.app/{key}/{title}/{body}?icon=https://i.imgur.com/8p4jA4w.png"

    # AI candidate review (OpenAI-compatible chat completions API)
    AI_ANALYSIS_ENABLED: bool = os.getenv("AI_ANALYSIS_ENABLED", "true").lower() == "true"
    AI_API_KEY: str = os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    AI_BASE_URL: str = os.getenv(
        "AI_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    ).rstrip("/")
    AI_MODEL: str = os.getenv("AI_MODEL", os.getenv("OPENAI_MODEL", ""))
    AI_TIMEOUT_SECONDS: int = min(120, max(5, int(os.getenv("AI_TIMEOUT_SECONDS", "45"))))
    AI_MAX_CANDIDATES: int = min(20, max(1, int(os.getenv("AI_MAX_CANDIDATES", "10"))))

    # Database
    DATABASE_HOST: str = os.getenv("DB_HOST", "localhost")
    DATABASE_PORT: str = os.getenv("DB_PORT", "5432")
    DATABASE_USER: str = os.getenv("DB_USER", "liangzou")
    DATABASE_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DATABASE_NAME: str = os.getenv("DB_NAME", "stock_db")
    DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

    # API Security
    API_TOKEN: Optional[str] = os.getenv("API_TOKEN")
    ENABLE_AUTH: bool = os.getenv("ENABLE_AUTH", "false").lower() == "true"
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")

    # CORS
    ALLOWED_ORIGINS: list = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"
    ).split(",")

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    RATE_LIMIT_SCAN: str = os.getenv("RATE_LIMIT_SCAN", "10/minute")
    RATE_LIMIT_SYNC: str = os.getenv("RATE_LIMIT_SYNC", "30/minute")
    RATE_LIMIT_AI: str = os.getenv("RATE_LIMIT_AI", "30/day")

    # Sentinel
    SENTINEL_DEFAULT_TIME: str = os.getenv("SENTINEL_DEFAULT_TIME", "09:30")
    SENTINEL_SCHEDULE_TIMES: str = os.getenv(
        "SENTINEL_SCHEDULE_TIMES", DEFAULT_SENTINEL_SCHEDULE_TIMES
    )

    # Scanning defaults
    DEFAULT_THRESHOLD: float = float(os.getenv("DEFAULT_THRESHOLD", "0.12"))
    DEFAULT_VOL_MULTIPLIER: float = float(os.getenv("DEFAULT_VOL_MULTIPLIER", "1.5"))
    DEFAULT_RSI_MIN: int = int(os.getenv("DEFAULT_RSI_MIN", "55"))
    DEFAULT_TURNOVER_MIN: float = float(os.getenv("DEFAULT_TURNOVER_MIN", "3.0"))

    # Market data
    AKSHARE_TIMEOUT: int = int(os.getenv("AKSHARE_TIMEOUT", "30"))
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "15"))
    TUSHARE_TOKEN: str = os.getenv("TUSHARE_TOKEN", "")


    # Proxy settings - disable system proxy to avoid connection errors
    # Set to "true" to disable proxy for AkShare requests
    DISABLE_PROXY: bool = os.getenv("DISABLE_PROXY", "true").lower() == "true"

    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    @classmethod
    def get_database_url(cls) -> str:
        """Get PostgreSQL connection URL."""
        if cls.DATABASE_URL:
            return cls.DATABASE_URL
        return f"postgresql://{cls.DATABASE_USER}:{cls.DATABASE_PASSWORD}@{cls.DATABASE_HOST}:{cls.DATABASE_PORT}/{cls.DATABASE_NAME}"

    @classmethod
    def is_bark_configured(cls) -> bool:
        """Check if Bark push notification is properly configured."""
        return bool(cls.BARK_KEY and "YOUR_BARK_KEY" not in cls.BARK_KEY and cls.BARK_KEY.strip())

    @classmethod
    def get_bark_safe_status(cls) -> dict:
        """Get Bark configuration status without exposing the key."""
        return {
            "configured": cls.is_bark_configured(),
            "sentinel_time": os.getenv("SENTINEL_DEFAULT_TIME", cls.SENTINEL_DEFAULT_TIME)
        }

    def is_ai_analysis_configured(self) -> bool:
        """Return whether the optional AI review layer can make requests."""
        if not self.AI_ANALYSIS_ENABLED or not self.AI_BASE_URL or not self.AI_MODEL:
            return False
        # Local OpenAI-compatible servers such as Ollama commonly need no API key.
        return bool(self.AI_API_KEY) or self.AI_BASE_URL.startswith(
            ("http://127.0.0.1", "http://localhost")
        )

    def get_ai_safe_status(self) -> dict:
        return {
            "enabled": self.AI_ANALYSIS_ENABLED,
            "configured": self.is_ai_analysis_configured(),
            "model": self.AI_MODEL or None,
            "provider": self.AI_BASE_URL.split("//", 1)[-1].split("/", 1)[0],
            "max_candidates": self.AI_MAX_CANDIDATES,
            "auth_enabled": self.ENABLE_AUTH,
            "security_warning": None if self.ENABLE_AUTH else "AI写接口未启用令牌校验，仅建议本机访问",
        }

    @classmethod
    def get_requests_proxies(cls) -> Optional[Dict[str, str]]:
        """
        Get proxy configuration for HTTP requests.

        Returns None to disable proxies (recommended for AkShare),
        or a dict of proxy URLs if proxies are needed.
        """
        if cls.DISABLE_PROXY:
            return None
        # Return empty dict to use system proxy, or specify custom proxy
        return {}

    @classmethod
    def setup_no_proxy(cls) -> None:
        """
        Disable proxy by setting environment variables.
        Call this at application startup to prevent proxy-related errors.
        """
        if cls.DISABLE_PROXY:
            os.environ['NO_PROXY'] = '*'
            os.environ['no_proxy'] = '*'
            os.environ['HTTP_PROXY'] = ''
            os.environ['HTTPS_PROXY'] = ''
            os.environ['http_proxy'] = ''
            os.environ['https_proxy'] = ''
        
        # Ensure fork safety on macOS is set during setup as well
        if platform.system() == "Darwin":
            os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"


# Global config instance
config = Config()
