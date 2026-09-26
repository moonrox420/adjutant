"""Synthetic persisted creative fixtures for browser tests, never a provider replacement."""

import io
from uuid import uuid4

import psycopg
from PIL import Image
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from adjutant.campaign_api import scene_graph
from adjutant.creative_concepts import CONCEPT_DIRECTIONS
from adjutant.security import digest
from adjutant.storage import ObjectStore
from adjutant.studio_operations import persist_render


def seed_concepts(admin_url: str, identity: dict, storage: ObjectStore) -> str:
    """Seed a completed set to exercise real read/edit/render/attach HTTP paths in Playwright."""
    if admin_url.rsplit("/", 1)[-1] != "adjutant_test":
        raise ValueError("Browser fixtures may only use adjutant_test")
    with psycopg.connect(admin_url, row_factory=dict_row) as conn:
        conn.execute("SET search_path=adjutant,public")
        user = conn.execute(
            "SELECT id FROM app_user WHERE email=%s", (identity["email"],)
        ).fetchone()["id"]
        brand = conn.execute(
            "INSERT INTO brand(account_id,display_name,website_url,vertical,campaigns_enabled) "
            "VALUES(%s,'Concept browser fixture','https://example.com','home_services',true) "
            "RETURNING id",
            (identity["account_id"],),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO budget_ceiling(brand_id,scope_kind,monthly_usd_max,daily_usd_max,set_by) "
            "VALUES(%s,'brand',5000,200,%s)",
            (brand, user),
        )
        conn.execute(
            "INSERT INTO channel_connection(brand_id,channel,external_ad_account_id,"
            "external_account_name,selected,health,verified_at) "
            "VALUES(%s,'meta','12345','Synthetic browser account',true,'healthy',now())",
            (brand,),
        )
        understanding = {
            "offers": ["Plumbing repairs"],
            "audience": "Homeowners",
            "voice": "Practical",
            "proof_points": [],
        }
        context = conn.execute(
            "INSERT INTO brand_context(brand_id,version,input_hash,source_kind,document) "
            "VALUES(%s,1,%s,'prompt',%s) RETURNING id",
            (brand, digest(understanding), Jsonb(understanding)),
        ).fetchone()["id"]
        root = uuid4()
        conn.execute(
            "INSERT INTO studio_draft(id,brand_id,actor_user_id,image_model) "
            "VALUES(%s,%s,%s,'browser-fixture')",
            (root, brand, user),
        )
        conn.execute(
            "INSERT INTO studio_job(id,brand_id,actor_id,session_hash,request_key,"
            "url_or_prompt,concept_count) "
            "VALUES(%s,%s,%s,'not-a-session',%s,'Synthetic browser fixture',5)",
            (root, brand, user, uuid4()),
        )
        for index, direction in enumerate(CONCEPT_DIRECTIONS):
            image = io.BytesIO()
            Image.new("RGB", (64, 64), (40 + index * 25, 100, 140)).save(
                image, format="PNG"
            )
            key = storage.put(brand, image.getvalue())
            document = {
                "understanding": understanding,
                "brand_name": "Concept browser fixture",
                "destination_url": "https://example.com",
                "context_version": 1,
                "concept": direction,
                "meta": {
                    "headline": direction["name"],
                    "primary_text": "Book a plumbing repair visit.",
                    "description": "Residential repairs.",
                    "cta": "Contact us",
                    "image_prompt": direction["brief"],
                },
                "google": {
                    "headlines": [
                        "Home Plumbing",
                        "Plumbing Repairs",
                        "Contact The Team",
                    ],
                    "descriptions": [
                        "Repair your home's plumbing.",
                        "Contact our team about an appointment.",
                    ],
                    "destination_path": "repairs",
                },
                "tiktok": {
                    "hook": "That dripping tap again?",
                    "visual_script": "A plumber inspecting a fixture.",
                    "cta": "Contact us",
                },
            }
            draft_id = root if index == 0 else uuid4()
            if index > 0:
                conn.execute(
                    "INSERT INTO studio_draft(id,brand_id,actor_user_id,image_model) "
                    "VALUES(%s,%s,%s,'browser-fixture')",
                    (draft_id, brand, user),
                )
            row = conn.execute(
                "UPDATE studio_draft SET job_id=%s,concept_index=%s,context_id=%s,"
                "state='completed',document=%s,scene_graph=%s,image_key=%s,"
                "image_mime='image/png' WHERE id=%s RETURNING *",
                (
                    root,
                    index,
                    context,
                    Jsonb(document),
                    Jsonb(scene_graph(document, key)),
                    key,
                    draft_id,
                ),
            ).fetchone()
            persist_render(conn, storage, brand, row, "1:1")
        conn.execute(
            "UPDATE studio_job SET state='completed',finished_at=now() WHERE id=%s",
            (root,),
        )
        return str(brand)
