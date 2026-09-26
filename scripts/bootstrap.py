"""Create an isolated local database, restricted runtime role, and first owner."""

import argparse
import base64
import getpass
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from migrate import migrate  # noqa: E402

from adjutant.security import (  # noqa: E402
    ApprovalSigner,
    generate_signing_key,
    password_hash,
)


def provision_runtime(conn: psycopg.Connection, app_password: str) -> None:
    """Apply the same runtime grants in development, tests, and deployments."""
    if not conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='adjutant_app'"
    ).fetchone():
        conn.execute(
            sql.SQL(
                "CREATE ROLE adjutant_app LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS"
            ).format(sql.Literal(app_password))
        )
    conn.execute("SET search_path=adjutant,public")
    conn.execute("GRANT USAGE ON SCHEMA adjutant TO adjutant_app")
    conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA adjutant TO adjutant_app")
    conn.execute(
        "REVOKE ALL ON local_credential,auth_session,login_attempt,account_token,mail_outbox "
        "FROM adjutant_app"
    )
    conn.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA adjutant TO adjutant_app"
    )
    conn.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA adjutant TO adjutant_app")
    conn.execute(
        "REVOKE EXECUTE ON FUNCTION consume_activity_batch(integer) FROM adjutant_app"
    )
    conn.execute("REVOKE EXECUTE ON FUNCTION reap_abandoned_jobs() FROM adjutant_app")
    conn.execute(
        "REVOKE EXECUTE ON FUNCTION lock_spend_authority(uuid,uuid) FROM adjutant_app"
    )
    writable = [
        "brand",
        "brand_graph_assertion",
        "brand_kit",
        "brand_constraint",
        "plan",
        "plan_allocation",
        "budget_ceiling",
        "guardrail",
        "brand_kill_switch",
        "approval_request",
        "creative_concept",
        "creative",
        "asset",
        "rendition",
        "compliance_record",
        "compliance_check",
        "agent_run",
        "brand_context",
        "studio_draft",
        "studio_rendition",
        "studio_plan_creative",
        "studio_job",
    ]
    conn.execute(
        sql.SQL("GRANT INSERT,UPDATE ON {} TO adjutant_app").format(
            sql.SQL(",").join(map(sql.Identifier, writable))
        )
    )
    conn.execute("GRANT DELETE ON plan_allocation TO adjutant_app")
    conn.execute("GRANT INSERT ON action,event_outbox,website_evidence TO adjutant_app")
    conn.execute("GRANT UPDATE(reverted_by_action_id) ON action TO adjutant_app")
    conn.execute(
        "GRANT UPDATE(status, activated_at, campaigns_enabled) ON brand TO adjutant_app"
    )
    conn.execute("REVOKE INSERT,UPDATE ON approval_token FROM adjutant_app")
    conn.execute(
        "GRANT UPDATE(voided_at,voided_reason) ON approval_token TO adjutant_app"
    )
    conn.execute(
        "REVOKE INSERT, UPDATE, DELETE ON launch_authorization, channel_launch_grant "
        "FROM adjutant_app"
    )
    conn.execute("REVOKE EXECUTE ON FUNCTION lock_runner_brand(uuid) FROM adjutant_app")


def provision_approval(conn: psycopg.Connection, password: str) -> None:
    """Permit issuance only through the dedicated, RLS-bound approval identity."""
    if not conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='adjutant_approval'"
    ).fetchone():
        conn.execute(
            sql.SQL(
                "CREATE ROLE adjutant_approval LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS"
            ).format(sql.Literal(password))
        )
    conn.execute("GRANT USAGE ON SCHEMA adjutant TO adjutant_approval")
    conn.execute("""GRANT SELECT ON adjutant.brand,adjutant.seat,adjutant.plan,
        adjutant.plan_allocation,adjutant.budget_ceiling,adjutant.brand_kill_switch,
        adjutant.channel_capability,adjutant.approval_request,adjutant.approval_token,
        adjutant.action,adjutant.event_outbox TO adjutant_approval""")
    conn.execute("GRANT UPDATE(updated_at) ON adjutant.brand TO adjutant_approval")
    conn.execute("GRANT UPDATE(state) ON adjutant.plan TO adjutant_approval")
    conn.execute("GRANT UPDATE ON adjutant.approval_request TO adjutant_approval")
    conn.execute("""GRANT INSERT ON adjutant.approval_token,adjutant.action,
        adjutant.event_outbox TO adjutant_approval""")
    conn.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA adjutant TO adjutant_approval")
    conn.execute("""GRANT EXECUTE ON FUNCTION adjutant.current_brand_ids(),
        adjutant.current_actor_id(),adjutant.authenticate_session(text),
        adjutant.lock_session(text) TO adjutant_approval""")


def provision_approval_files(local: Path) -> str:
    """Create separate service credentials once without replacing the existing signing key."""
    for name in ("approval.password", "approval-service.secret"):
        target = local / name
        if not target.exists():
            with target.open("x", encoding="utf-8") as handle:
                handle.write(secrets.token_urlsafe(32))
            target.chmod(0o600)
    return (local / "approval.password").read_text(encoding="utf-8").strip()


def provision_worker(conn: psycopg.Connection, worker_password: str) -> None:
    """The delivery role cannot read credentials, sessions, or raw brand tables."""
    if not conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='adjutant_worker'"
    ).fetchone():
        conn.execute(
            sql.SQL(
                "CREATE ROLE adjutant_worker LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS"
            ).format(sql.Literal(worker_password))
        )
    conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA adjutant FROM adjutant_worker")
    conn.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA adjutant FROM adjutant_worker")
    conn.execute("GRANT USAGE ON SCHEMA adjutant TO adjutant_worker")
    conn.execute("GRANT SELECT,UPDATE ON adjutant.mail_outbox TO adjutant_worker")
    conn.execute(
        "GRANT SELECT,INSERT,UPDATE ON adjutant.consumer_process TO adjutant_worker"
    )
    conn.execute(
        "GRANT EXECUTE ON FUNCTION adjutant.consume_activity_batch(integer) TO adjutant_worker"
    )
    conn.execute(
        "GRANT EXECUTE ON FUNCTION adjutant.reap_abandoned_jobs() TO adjutant_worker"
    )


def provision_gateway(conn: psycopg.Connection, password: str) -> None:
    """Grant verification reads and append-only reservations, without approval issuance rights."""
    if not conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='adjutant_gateway'"
    ).fetchone():
        conn.execute(
            sql.SQL(
                "CREATE ROLE adjutant_gateway LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS"
            ).format(sql.Literal(password))
        )
    conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA adjutant FROM adjutant_gateway")
    conn.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA adjutant FROM adjutant_gateway")
    conn.execute("GRANT USAGE ON SCHEMA adjutant TO adjutant_gateway")
    conn.execute("""GRANT SELECT ON adjutant.brand,adjutant.approval_token,
        adjutant.approval_request,adjutant.plan,adjutant.plan_allocation,
        adjutant.brand_kill_switch,adjutant.budget_ceiling,adjutant.approval_token_consumption
        TO adjutant_gateway""")
    conn.execute(
        "GRANT INSERT ON adjutant.approval_token_consumption TO adjutant_gateway"
    )
    conn.execute(
        "GRANT SELECT ON adjutant.guardrail,adjutant.channel_connection,"
        "adjutant.launch_authorization,adjutant.channel_launch_grant,"
        "adjutant.launch_authorization_void TO adjutant_gateway"
    )
    conn.execute("GRANT INSERT ON adjutant.channel_launch_grant TO adjutant_gateway")
    conn.execute(
        "GRANT SELECT ON adjutant.creative,adjutant.creative_concept,adjutant.rendition,"
        "adjutant.studio_plan_creative,adjutant.studio_rendition,adjutant.asset,"
        "adjutant.placement_spec TO adjutant_gateway"
    )
    conn.execute(
        "GRANT EXECUTE ON FUNCTION adjutant.campaign_review_manifest(uuid,uuid) TO adjutant_gateway"
    )
    conn.execute("REVOKE SELECT ON adjutant.seat FROM adjutant_gateway")
    conn.execute(
        "GRANT UPDATE(status, activated_at, campaigns_enabled) "
        "ON adjutant.brand TO adjutant_gateway"
    )
    conn.execute(
        "GRANT INSERT ON adjutant.action,adjutant.event_outbox TO adjutant_gateway"
    )
    conn.execute("GRANT SELECT(id) ON adjutant.action TO adjutant_gateway")
    conn.execute(
        "GRANT UPDATE(reverted_by_action_id) ON adjutant.action TO adjutant_gateway"
    )
    conn.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA adjutant TO adjutant_gateway")
    conn.execute(
        "GRANT EXECUTE ON FUNCTION adjutant.lock_runner_brand(uuid) TO adjutant_gateway"
    )
    conn.execute("""GRANT EXECUTE ON FUNCTION adjutant.current_brand_ids(),
        adjutant.lock_spend_authority(uuid,uuid) TO adjutant_gateway""")


def provision_gateway_files(local: Path) -> str:
    """Create service credentials once and export the existing signer's public verification key."""
    for name in ("gateway.password", "gateway-service.secret"):
        target = local / name
        if not target.exists():
            with target.open("x", encoding="utf-8") as handle:
                handle.write(secrets.token_urlsafe(32))
            target.chmod(0o600)
    signer = ApprovalSigner(local / "approval.key")
    key_file = local / "approval-public-keys.json"
    keys = json.loads(key_file.read_text(encoding="utf-8")) if key_file.exists() else {}
    keys[signer.key_id] = base64.b64encode(signer.public_bytes).decode("ascii")
    key_file.write_text(json.dumps(keys, indent=2) + "\n", encoding="utf-8")
    return (local / "gateway.password").read_text(encoding="utf-8").strip()


def write_private_file(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def append_private_file(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def ensure_cluster_roles(
    conn: psycopg.Connection, role_passwords: dict[str, str]
) -> None:
    """Ensure all required database roles exist in the cluster prior to running migrations."""
    for role_name, password in role_passwords.items():
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (role_name,)
        ).fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS"
                ).format(sql.Identifier(role_name), sql.Literal(password))
            )


def bootstrap(email: str, password: str, account_type: str) -> None:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    local = root / ".local"
    local.mkdir(exist_ok=True, mode=0o700)
    admin_password = (local / "postgres.password").read_text().strip()
    server = f"postgresql://adjutant_admin:{quote(admin_password)}@127.0.0.1:55439"

    key_path = local / "approval.key"
    if not key_path.exists():
        generate_signing_key(key_path)
    gateway_password = provision_gateway_files(local)
    approval_password = provision_approval_files(local)
    app_password_file = local / "app.password"
    if not app_password_file.exists():
        write_private_file(app_password_file, secrets.token_urlsafe(32))
    app_password = app_password_file.read_text().strip()
    worker_password_file = local / "worker.password"
    if not worker_password_file.exists():
        write_private_file(worker_password_file, secrets.token_urlsafe(32))
    worker_password = worker_password_file.read_text().strip()

    role_passwords = {
        "adjutant_app": app_password,
        "adjutant_worker": worker_password,
        "adjutant_gateway": gateway_password,
        "adjutant_approval": approval_password,
    }

    with psycopg.connect(server + "/postgres", autocommit=True) as conn:
        for name in ("adjutant", "adjutant_test"):
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        ensure_cluster_roles(conn, role_passwords)

    admin_url = server + "/adjutant"
    write_private_file(local / "admin.url", admin_url)
    write_private_file(local / "test-admin.url", server + "/adjutant_test")
    migrate(admin_url)
    with psycopg.connect(admin_url) as conn:
        provision_runtime(conn, app_password)
        provision_worker(conn, worker_password)
        provision_gateway(conn, gateway_password)
        provision_approval(conn, approval_password)
        existing = conn.execute(
            "SELECT id FROM app_user WHERE email=%s", (email,)
        ).fetchone()
        if existing is None:
            user_row = conn.execute(
                "INSERT INTO app_user(email,full_name,email_verified_at) "
                "VALUES(%s,%s,now()) RETURNING id",
                (email, "Workspace owner"),
            ).fetchone()
            if not user_row:
                raise RuntimeError("Failed to create workspace owner")
            user = user_row[0]
            account_row = conn.execute(
                """INSERT INTO account(account_type,display_name)
                                    VALUES(%s,'My workspace') RETURNING id""",
                (account_type,),
            ).fetchone()
            if not account_row:
                raise RuntimeError("Failed to create workspace account")
            account = account_row[0]
            conn.execute(
                "INSERT INTO local_credential VALUES(%s,%s)",
                (user, password_hash(password)),
            )
            conn.execute(
                """INSERT INTO seat(account_id,user_id,role,accepted_at,
                         approval_daily_usd_cap,approval_total_usd_cap)
                         VALUES(%s,%s,'owner',now(),10000,300000)""",
                (account, user),
            )
            print(
                "Created workspace owner. No sample campaigns or performance data were inserted."
            )
        else:
            print("Owner already exists; password and account data were preserved.")
    app_url = (
        f"postgresql://adjutant_app:{quote(app_password)}@127.0.0.1:55439/adjutant"
    )
    env_file = root / ".env"
    if not env_file.exists():
        write_private_file(
            env_file,
            f"ADJUTANT_DATABASE_URL={app_url}\nADJUTANT_PUBLIC_ORIGIN=http://localhost:3000\n",
        )
    env_text = env_file.read_text(encoding="utf-8")
    if "ADJUTANT_WORKER_DATABASE_URL=" not in env_text:
        append_private_file(
            env_file,
            f"\nADJUTANT_WORKER_DATABASE_URL=postgresql://adjutant_worker:{quote(worker_password)}@127.0.0.1:55439/adjutant\n",
        )
    if "ADJUTANT_GATEWAY_DATABASE_URL=" not in env_text:
        append_private_file(
            env_file,
            f"\nADJUTANT_GATEWAY_DATABASE_URL=postgresql://adjutant_gateway:"
            f"{quote(gateway_password)}@127.0.0.1:55439/adjutant\n",
        )
    if "ADJUTANT_APPROVAL_DATABASE_URL=" not in env_text:
        append_private_file(
            env_file,
            f"\nADJUTANT_APPROVAL_DATABASE_URL=postgresql://adjutant_approval:"
            f"{quote(approval_password)}@127.0.0.1:55439/adjutant\n",
        )
    print("Database migrations, runtime permissions, and approval keys are ready.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-file", type=Path)
    parser.add_argument(
        "--account-type", choices=["business", "agency"], default="business"
    )
    args = parser.parse_args()
    password = (
        args.password_file.read_text().strip()
        if args.password_file
        else getpass.getpass("Owner password (at least 15 characters): ")
    )
    if len(password) < 15 or len(password) > 256:
        raise SystemExit("Password must contain 15–256 characters")
    bootstrap(args.email, password, args.account_type)
