"""Upgrade an existing Windows workspace without creating accounts or resetting credentials."""

from pathlib import Path
from urllib.parse import quote

import psycopg
from bootstrap import (
    provision_approval,
    provision_approval_files,
    provision_gateway,
    provision_gateway_files,
    provision_runtime,
    provision_worker,
)
from migrate import migrate

ROOT = Path(__file__).resolve().parents[1]


def upgrade() -> None:
    """Apply checked migrations and service grants to the existing working database only."""
    local = ROOT / ".local"
    admin_url = (local / "admin.url").read_text(encoding="utf-8").strip()
    if admin_url.rsplit("/", 1)[-1] != "adjutant":
        raise ValueError("Workspace upgrades require the adjutant database")
    app_password = (local / "app.password").read_text(encoding="utf-8").strip()
    worker_password = (local / "worker.password").read_text(encoding="utf-8").strip()
    migrate(admin_url)
    gateway_password = provision_gateway_files(local)
    approval_password = provision_approval_files(local)
    with psycopg.connect(admin_url) as conn:
        provision_runtime(conn, app_password)
        provision_worker(conn, worker_password)
        provision_gateway(conn, gateway_password)
        provision_approval(conn, approval_password)
    env_path = ROOT / ".env"
    content = env_path.read_text(encoding="utf-8")
    keys = {
        line.split("=", 1)[0].strip() for line in content.splitlines() if "=" in line
    }
    if "ADJUTANT_GATEWAY_DATABASE_URL" not in keys:
        host = admin_url.split("@", 1)[1]
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\nADJUTANT_GATEWAY_DATABASE_URL=postgresql://adjutant_gateway:"
                f"{quote(gateway_password)}@{host}\n"
            )
    if "ADJUTANT_APPROVAL_DATABASE_URL" not in keys:
        host = admin_url.split("@", 1)[1]
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\nADJUTANT_APPROVAL_DATABASE_URL=postgresql://adjutant_approval:"
                f"{quote(approval_password)}@{host}\n"
            )
    print(
        "Workspace upgraded. Existing accounts, passwords, and campaign data were preserved."
    )


if __name__ == "__main__":
    upgrade()
