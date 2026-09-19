from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Explicit runtime configuration; secrets are never included in repr output."""

    model_config = SettingsConfigDict(env_prefix="ADJUTANT_", env_file=".env", extra="ignore")
    database_url: SecretStr
    signing_key_path: Path = Path(".local/approval.key")
    public_origin: str = "http://localhost:3000"
    secure_cookies: bool = False
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = ""
    ollama_provider: Literal["local", "cloud"] = "local"
    ollama_cloud_model: str = ""
    ollama_cloud_api_key: SecretStr = SecretStr("")
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    registry_path: Path = Path(__file__).with_name("event_registry.json")
    worker_database_url: SecretStr | None = None
    gateway_url: str = "http://127.0.0.1:8002"
    gateway_service_secret_path: Path = Path(".local/gateway-service.secret")
    approval_url: str = "http://127.0.0.1:8003"
    approval_service_secret_path: Path = Path(".local/approval-service.secret")
    mail_transport: Literal["file", "smtp"] = "file"
    mail_directory: Path = Path(".local/mail")
    mail_from: str = "Adjutant <accounts@adjutant.local>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: Literal["starttls", "ssl"] = "starttls"

    @model_validator(mode="after")
    def validate_delivery(self) -> "Settings":
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path not in {"", "/"}
        ):
            raise ValueError(
                "PUBLIC_ORIGIN must be an HTTP(S) origin without credentials or a path"
            )
        self.public_origin = self.public_origin.rstrip("/")
        approval = urlsplit(self.approval_url)
        if (
            approval.scheme not in {"http", "https"}
            or not approval.hostname
            or approval.username
            or approval.password
            or approval.query
            or approval.fragment
            or approval.path not in {"", "/"}
            or (
                approval.scheme == "http"
                and approval.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError("APPROVAL_URL requires HTTPS outside loopback and must be an origin")
        gateway = urlsplit(self.gateway_url)
        if (
            gateway.scheme not in {"http", "https"}
            or not gateway.hostname
            or gateway.username
            or gateway.password
            or gateway.query
            or gateway.fragment
            or gateway.path not in {"", "/"}
            or (
                gateway.scheme == "http"
                and gateway.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError("GATEWAY_URL requires HTTPS outside loopback and must be an origin")
        if self.mail_transport == "smtp" and not self.smtp_host:
            raise ValueError("SMTP_HOST is required for SMTP account delivery")
        if origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            if origin.scheme != "https" or not self.secure_cookies:
                raise ValueError("Consumer deployments require HTTPS and SECURE_COOKIES=true")
            if self.mail_transport != "smtp" or not self.worker_database_url:
                raise ValueError("Consumer deployments require SMTP and WORKER_DATABASE_URL")
        return self
