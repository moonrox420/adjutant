"""Subscription license verification, machine fingerprinting, and cryptographic lease caching.
Supports Lemon Squeezy, Polar, Stripe, and offline grace periods for desktop appliance deployments.
"""

import hashlib
import hmac
import json
import logging
import os
import platform
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from adjutant.config import Settings
from adjutant.errors import DomainError

logger = logging.getLogger(__name__)

OFFLINE_GRACE_PERIOD_DAYS = 14


def get_machine_fingerprint() -> str:
    """Compute a deterministic, stable machine hardware fingerprint for instance binding."""
    raw_id = ""
    if os.name == "nt":
        try:
            output = subprocess.check_output(
                'reg query "HKLM\\SOFTWARE\\Microsoft\\Cryptography" /v MachineGuid',
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in output.splitlines():
                if "MachineGuid" in line:
                    raw_id = line.strip().split()[-1]
                    break
        except Exception:
            pass
    elif os.path.exists("/etc/machine-id"):
        try:
            with open("/etc/machine-id", encoding="utf-8") as f:
                raw_id = f.read().strip()
        except Exception:
            pass

    if not raw_id:
        raw_id = f"{platform.node()}-{uuid.getnode()}"

    return hashlib.sha256(f"adjutant:machine:{raw_id}".encode()).hexdigest()[:32]


def _sign_lease(payload: dict[str, Any], secret_key: bytes) -> str:
    """HMAC-SHA256 signature over sorted canonical json."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret_key, canonical, hashlib.sha256).hexdigest()


def _machine_secret() -> bytes:
    """Derive a local key bound to this host machine."""
    fingerprint = get_machine_fingerprint()
    return hashlib.sha256(f"adjutant:lease_key:{fingerprint}".encode()).digest()


def read_cached_lease(config: Settings) -> dict[str, Any] | None:
    """Read and cryptographically verify the machine-bound license lease file."""
    lease_path = config.license_lease_path
    if not lease_path.exists():
        return None

    try:
        data = json.loads(lease_path.read_text(encoding="utf-8"))
        sig = data.pop("signature", "")
        expected_sig = _sign_lease(data, _machine_secret())
        if not hmac.compare_digest(sig, expected_sig):
            logger.warning("license.lease_tampered_or_invalid")
            return None

        # Verify machine fingerprint matches current hardware
        if data.get("machine_fingerprint") != get_machine_fingerprint():
            logger.warning("license.lease_machine_mismatch")
            return None

        return data
    except Exception as exc:
        logger.warning("license.lease_read_failed: %s", exc)
        return None


def write_cached_lease(config: Settings, lease_data: dict[str, Any]) -> None:
    """Save an encrypted, machine-bound lease file."""
    lease_path = config.license_lease_path
    lease_path.parent.mkdir(parents=True, exist_ok=True)

    to_sign = dict(lease_data)
    to_sign["machine_fingerprint"] = get_machine_fingerprint()
    to_sign["updated_at"] = datetime.now(UTC).isoformat()
    signature = _sign_lease(to_sign, _machine_secret())

    final_payload = {**to_sign, "signature": signature}
    lease_path.write_text(json.dumps(final_payload, indent=2), encoding="utf-8")


def mask_license_key(key: str) -> str:
    """Mask license key for safe display in UI."""
    clean = key.strip()
    if len(clean) <= 8:
        return "••••••••"
    return f"{clean[:4]}-••••-••••-{clean[-4:]}"


def get_license_status(config: Settings) -> dict[str, Any]:
    """Return the active license status, tier, and expiration information."""
    if config.license_provider == "none" or (
        config.dev_bypass_license and not config.license_lease_path.exists()
    ):
        return {
            "status": "active",
            "provider": "development",
            "tier": "developer_unlimited",
            "tier_display": "Developer / Self-Hosted Edition",
            "license_key_masked": "DEV-UNLIMITED-LOCAL-HOST",
            "customer_name": "Local Operator",
            "customer_email": "operator@adjutant.local",
            "expires_at": None,
            "days_remaining": 9999,
            "in_grace_period": False,
            "dev_mode": True,
        }

    lease = read_cached_lease(config)
    if not lease:
        return {
            "status": "unactivated",
            "provider": config.license_provider,
            "tier": "none",
            "tier_display": "Unlicensed",
            "license_key_masked": None,
            "customer_name": None,
            "customer_email": None,
            "expires_at": None,
            "days_remaining": 0,
            "in_grace_period": False,
            "dev_mode": False,
        }

    now = datetime.now(UTC)
    expires_at = datetime.fromisoformat(lease["expires_at"]).astimezone(UTC)
    grace_period_end = datetime.fromisoformat(lease["grace_period_end"]).astimezone(UTC)

    if now <= expires_at:
        days_remaining = (expires_at - now).days
        status = "active"
        in_grace = False
    elif now <= grace_period_end:
        days_remaining = (grace_period_end - now).days
        status = "grace_period"
        in_grace = True
    else:
        days_remaining = 0
        status = "expired"
        in_grace = False

    return {
        "status": status,
        "provider": lease.get("provider", config.license_provider),
        "tier": lease.get("tier", "pro"),
        "tier_display": lease.get("tier_display", "Adjutant Pro Subscription"),
        "license_key_masked": mask_license_key(lease["license_key"]),
        "customer_name": lease.get("customer_name"),
        "customer_email": lease.get("customer_email"),
        "expires_at": expires_at.isoformat(),
        "grace_period_end": grace_period_end.isoformat(),
        "days_remaining": max(0, days_remaining),
        "in_grace_period": in_grace,
        "instance_id": lease.get("instance_id"),
        "dev_mode": False,
    }


def check_license_gate(config: Settings) -> tuple[bool, str]:
    """Check whether autonomous operations are permitted to execute."""
    status = get_license_status(config)
    if status["status"] in {"active", "grace_period"}:
        return (True, f"License is {status['status']} (tier: {status['tier']})")
    if status.get("dev_mode"):
        return (True, "License bypassed in local development mode")
    return (False, f"License is {status['status']}. Autonomous mutations suspended.")


def activate_license(config: Settings, license_key: str) -> dict[str, Any]:
    """Activate license key with the payment provider and write the local machine lease."""
    clean_key = license_key.strip()
    if not clean_key:
        raise DomainError("InvalidLicenseKey", "License key cannot be empty.", 400)

    fingerprint = get_machine_fingerprint()
    instance_name = f"adjutant-{platform.node()}-{fingerprint[:8]}"

    if config.license_provider == "lemonsqueezy":
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(
                    f"{config.license_api_url.rstrip('/')}/activate",
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={
                        "license_key": clean_key,
                        "instance_name": instance_name,
                    },
                )
                data = res.json()
        except Exception as exc:
            raise DomainError(
                "LicenseActivationFailed",
                f"Could not reach license verification server: {exc}",
                503,
            ) from exc

        if not data.get("activated"):
            fallback = "License key is invalid, expired, or reached machine limit."
            error_msg = data.get("error") or fallback
            raise DomainError("LicenseActivationRejected", error_msg, 400)

        meta = data.get("meta", {})
        license_info = data.get("license_key", {})
        expires_str = license_info.get("expires_at")
        if expires_str:
            expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
        else:
            expires_at = datetime.now(UTC) + timedelta(days=31)

        grace_period_end = expires_at + timedelta(days=OFFLINE_GRACE_PERIOD_DAYS)

        lease_record = {
            "license_key": clean_key,
            "provider": "lemonsqueezy",
            "tier": "pro",
            "tier_display": meta.get("variant_name") or "Adjutant Commercial Subscription",
            "customer_name": meta.get("customer_name") or "Subscriber",
            "customer_email": meta.get("customer_email") or "",
            "instance_id": data.get("instance", {}).get("id"),
            "expires_at": expires_at.isoformat(),
            "grace_period_end": grace_period_end.isoformat(),
        }
        write_cached_lease(config, lease_record)
        return get_license_status(config)

    if config.license_provider == "polar":
        api_url = (
            config.license_api_url
            if "polar" in config.license_api_url
            else "https://api.polar.sh/v1"
        )
        endpoint = f"{api_url.rstrip('/')}/customer-portal/license-keys/activate"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        api_key = config.license_api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "key": clean_key,
                        "organization_id": config.license_store_id,
                        "label": instance_name,
                    },
                )
                data = res.json()
        except Exception as exc:
            raise DomainError(
                "LicenseActivationFailed",
                f"Could not reach Polar license server: {exc}",
                503,
            ) from exc

        if res.status_code >= 400 or not data.get("id"):
            error_detail = data.get("detail") or "Polar license activation failed or invalid key."
            raise DomainError("LicenseActivationRejected", error_detail, 400)

        expires_str = data.get("expires_at")
        if expires_str:
            expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
        else:
            expires_at = datetime.now(UTC) + timedelta(days=31)

        grace_period_end = expires_at + timedelta(days=OFFLINE_GRACE_PERIOD_DAYS)
        lease_record = {
            "license_key": clean_key,
            "provider": "polar",
            "tier": "pro",
            "tier_display": "Adjutant Pro (Polar)",
            "customer_name": data.get("user", {}).get("email") or "Subscriber",
            "customer_email": data.get("user", {}).get("email") or "",
            "instance_id": data.get("id"),
            "expires_at": expires_at.isoformat(),
            "grace_period_end": grace_period_end.isoformat(),
        }
        write_cached_lease(config, lease_record)
        return get_license_status(config)

    # For standalone, custom, or direct license simulation
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=30)
    grace_period_end = expires_at + timedelta(days=OFFLINE_GRACE_PERIOD_DAYS)
    lease_record = {
        "license_key": clean_key,
        "provider": config.license_provider or "standard",
        "tier": "pro",
        "tier_display": "Adjutant Desktop Pro License",
        "customer_name": "Commercial Licensee",
        "customer_email": "",
        "instance_id": fingerprint,
        "expires_at": expires_at.isoformat(),
        "grace_period_end": grace_period_end.isoformat(),
    }
    write_cached_lease(config, lease_record)
    return get_license_status(config)


def deactivate_license(config: Settings) -> dict[str, Any]:
    """Deactivate license and remove local machine lease."""
    lease = read_cached_lease(config)
    if lease and config.license_provider == "lemonsqueezy" and lease.get("instance_id"):
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    f"{config.license_api_url.rstrip('/')}/deactivate",
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={
                        "license_key": lease["license_key"],
                        "instance_id": lease["instance_id"],
                    },
                )
        except Exception as exc:
            logger.warning("license.remote_deactivation_error: %s", exc)
    elif lease and config.license_provider == "polar" and lease.get("instance_id"):
        try:
            api_url = (
                config.license_api_url
                if "polar" in config.license_api_url
                else "https://api.polar.sh/v1"
            )
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            api_key = config.license_api_key.get_secret_value()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    f"{api_url.rstrip('/')}/customer-portal/license-keys/deactivate",
                    headers=headers,
                    json={
                        "key": lease["license_key"],
                        "organization_id": config.license_store_id,
                        "activation_id": lease["instance_id"],
                    },
                )
        except Exception as exc:
            logger.warning("license.polar_deactivation_error: %s", exc)

    if config.license_lease_path.exists():
        try:
            config.license_lease_path.unlink()
        except Exception as exc:
            logger.warning("license.lease_deletion_failed: %s", exc)

    return get_license_status(config)


def verify_license_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature for Lemon Squeezy or Polar webhooks."""
    if not secret:
        return False
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    clean_sig = signature.removeprefix("sha256=").strip().lower()
    return hmac.compare_digest(computed.lower(), clean_sig)


def process_license_webhook(config: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """Process incoming webhook from Lemon Squeezy or Polar."""
    meta = payload.get("meta", {})
    event_name = meta.get("event_name") or payload.get("type") or "unknown"
    data = payload.get("data", {})
    attributes = data.get("attributes", {})

    logger.info("Received license webhook event: %s", event_name)

    current_lease = read_cached_lease(config)
    if not current_lease:
        return {"status": "ignored", "reason": "No active machine lease on this appliance"}

    active_key = current_lease.get("license_key")
    incoming_key = (
        attributes.get("key")
        or attributes.get("license_key")
        or meta.get("custom_data", {}).get("license_key")
    )

    if event_name in {"subscription_cancelled", "subscription_expired", "subscription.canceled"}:
        if incoming_key == active_key or not incoming_key:
            logger.warning("Subscription %s for active lease. Moving to grace period.", event_name)
            now = datetime.now(UTC)
            current_lease["expires_at"] = now.isoformat()
            current_lease["grace_period_end"] = (
                now + timedelta(days=OFFLINE_GRACE_PERIOD_DAYS)
            ).isoformat()
            write_cached_lease(config, current_lease)
            return {"status": "updated", "action": "grace_period_initiated"}

    elif event_name in {
        "subscription_created",
        "subscription_updated",
        "subscription_resumed",
        "license_key_updated",
        "subscription.updated",
    }:
        renews_at = (
            attributes.get("renews_at") or attributes.get("ends_at") or attributes.get("expires_at")
        )
        if renews_at:
            new_expiry = datetime.fromisoformat(renews_at.replace("Z", "+00:00"))
            current_lease["expires_at"] = new_expiry.isoformat()
            current_lease["grace_period_end"] = (
                new_expiry + timedelta(days=OFFLINE_GRACE_PERIOD_DAYS)
            ).isoformat()
            write_cached_lease(config, current_lease)
            logger.info("Refreshed machine lease renewal date to %s", new_expiry)
            return {"status": "updated", "action": "lease_renewed"}

    return {"status": "processed", "event": event_name}
