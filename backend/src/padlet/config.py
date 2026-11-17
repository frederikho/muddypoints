"""Runtime settings, read from environment variables (set by template.yaml / dev_server.py).

Read lazily on every access so tests can monkeypatch os.environ.
"""
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class Settings:
    @property
    def table_name(self) -> str:
        return _env("TABLE_NAME", "padlet-dev")

    @property
    def bucket_name(self) -> str:
        return _env("BUCKET_NAME", "padlet-dev-media")

    @property
    def secret_key(self) -> str:
        return _env("SECRET_KEY", "dev-secret-change-me")

    @property
    def frontend_url(self) -> str:
        """Base URL of the static frontend, without trailing slash (used in emails and board links)."""
        return _env("FRONTEND_URL", "http://localhost:8787").rstrip("/")

    @property
    def mail_from(self) -> str:
        """Verified SES sender address. Empty = emails are only written to the log (dev mode)."""
        return _env("MAIL_FROM")

    @property
    def mail_from_name(self) -> str:
        return _env("MAIL_FROM_NAME", "Course Boards")

    @property
    def brevo_api_key(self) -> str:
        """If set, emails go through Brevo instead of SES."""
        return _env("BREVO_API_KEY")

    @property
    def allowed_email_domains(self) -> list[str]:
        """Comma separated, e.g. 'uni-example.de,student.uni-example.de'. Empty = anyone may register."""
        return [d.strip().lower().lstrip("@") for d in _env("ALLOWED_EMAIL_DOMAINS").split(",") if d.strip()]

    @property
    def log_level(self) -> str:
        return _env("LOG_LEVEL", "INFO").upper()

    @property
    def log_group(self) -> str:
        return _env("LOG_GROUP_NAME") or _env("AWS_LAMBDA_LOG_GROUP_NAME")

    @property
    def region(self) -> str:
        return _env("AWS_REGION") or _env("AWS_DEFAULT_REGION", "eu-central-1")

    @property
    def endpoint_url(self) -> str:
        """Set only in local dev/tests (moto server)."""
        return _env("AWS_ENDPOINT_URL")


settings = Settings()

SESSION_TTL_SECONDS = 14 * 24 * 3600
ANON_TTL_SECONDS = 7 * 24 * 3600
RESET_TTL_SECONDS = 3600
UPLOAD_URL_TTL_SECONDS = 3600
MEDIA_URL_TTL_SECONDS = 6 * 3600
MAX_COMMENT_LENGTH = 2000
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
MAX_PAGES_PER_UPLOAD = 300
SLIDE_MAX_SIDE_PX = 2000
