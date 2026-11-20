"""Settings from environment variables (12-factor). Defaults suit local development."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://returnright:returnright@localhost:5432/returnright"))
    gateway_url: str = field(default_factory=lambda: os.environ.get("GATEWAY_URL", "http://localhost:8081"))
    gateway_timeout_s: float = field(default_factory=lambda: float(os.environ.get("GATEWAY_TIMEOUT_S", "8")))
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", "dev-only-secret-change-me-in-production-0123456789"))
    staff_token_minutes: int = field(default_factory=lambda: int(os.environ.get("STAFF_TOKEN_MINUTES", "480")))
    customer_token_minutes: int = field(default_factory=lambda: int(os.environ.get("CUSTOMER_TOKEN_MINUTES", "60")))
    otp_ttl_minutes: int = field(default_factory=lambda: int(os.environ.get("OTP_TTL_MINUTES", "10")))
    expiry_days: int = field(default_factory=lambda: int(os.environ.get("EXPIRY_DAYS", "14")))
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", False))
    carrier_delay_s: int = field(default_factory=lambda: int(os.environ.get("CARRIER_DELAY_SECONDS", "120")))
    jobs_enabled: bool = field(default_factory=lambda: _bool("JOBS_ENABLED", True))
    job_interval_s: int = field(default_factory=lambda: int(os.environ.get("JOB_INTERVAL_SECONDS", "15")))
    system_token: str = field(default_factory=lambda: os.environ.get("SYSTEM_TOKEN", "dev-system-token"))
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "1025")))
    mail_from: str = field(default_factory=lambda: os.environ.get("MAIL_FROM", "returns@returnright.test"))
    upload_dir: str = field(default_factory=lambda: os.environ.get("UPLOAD_DIR", "uploads"))
    max_upload_mb: int = field(default_factory=lambda: int(os.environ.get("MAX_UPLOAD_MB", "5")))
    demo_staff_password: str = field(default_factory=lambda: os.environ.get("DEMO_STAFF_PASSWORD", "demo-pass"))
