import json
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from psycopg.types.json import Jsonb
from pydantic import Field, SecretStr

from adjutant.config import Settings
from adjutant.creative_policy import enforce_blocked_claims
from adjutant.credentials import CredentialStore
from adjutant.db import Database, Principal, one, require_role
from adjutant.errors import DomainError
from adjutant.events import EventRegistry
from adjutant.models import Input
from adjutant.rendering import SIZES, render_ad
from adjutant.security import digest
from adjutant.service import EDIT_ROLES, audit, generation_gate, locked_brand
from adjutant.services.visuals import VisualsGenerator, validate_image_model
from adjutant.storage import ObjectStore
from adjutant.studio_models import BrandUnderstanding


class VisualConfiguration(Input):
    api_key: SecretStr | None = Field(default=None, min_length=10, max_length=512)
    model: str = Field(min_length=4, max_length=120, pattern=r"^[a-zA-Z0-9._-]+$")


class ContextEdit(Input):
    expected_version: int = Field(ge=1)
    document: BrandUnderstanding


class RenderInput(Input):
    expected_revision: int = Field(ge=1)
    aspect_ratio: Literal["1:1", "4:5", "9:16", "16:9"] = "1:1"


class AttachInput(Input):
    expected_revision: int = Field(ge=1)
    plan_id: UUID


def visual_generator(
    conn: psycopg.Connection[Any], brand_id: UUID, config: Settings
) -> VisualsGenerator:
    configured = conn.execute(
        "SELECT 1 FROM tenant_secret WHERE brand_id=%s AND name='visual_provider'",
        (brand_id,),
    ).fetchone()
    if configured:
        value = json.loads(
            CredentialStore(config.credential_master_key_path).read(
                conn, brand_id, "visual_provider"
            )
        )
        return VisualsGenerator(value["api_key"], model=value["model"])
    return VisualsGenerator(
        config.gemini_api_key.get_secret_value(), model=config.gemini_image_model
    )


def persist_render(
    conn: psycopg.Connection[Any],
    storage: ObjectStore,
    brand_id: UUID,
    row: dict,
    ratio: str,
) -> dict:
    """Store a validated revision-specific PNG, SVG, and editable scene atomically."""
    enforce_blocked_claims(conn, brand_id, row["document"])
    existing = conn.execute(
        "SELECT * FROM studio_rendition WHERE brand_id=%s AND draft_id=%s "
        "AND draft_revision=%s AND aspect_ratio=%s",
        (brand_id, row["id"], row["revision"], ratio),
    ).fetchone()
    if existing:
        return existing
    result = render_ad(row["document"], storage.read(brand_id, row["image_key"]), ratio)
    result.scene["layers"][0]["asset_key"] = row["image_key"]
    return one(
        conn,
        "INSERT INTO studio_rendition(brand_id,draft_id,draft_revision,aspect_ratio,"
        "png_key,svg_key,scene_graph,width,height) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            brand_id,
            row["id"],
            row["revision"],
            ratio,
            storage.put(brand_id, result.png),
            storage.put(brand_id, result.svg),
            Jsonb(result.scene),
            result.width,
            result.height,
        ),
    )


def studio_operations_router(
    db: Database,
    config: Settings,
    storage: ObjectStore,
    events: EventRegistry,
    authenticate: Callable[[Request], Principal],
):
    router = APIRouter(prefix="/api/brands/{brand_id}", tags=["studio-operations"])

    @router.get("/visual-provider")
    def provider_status(brand_id: UUID, actor: Principal = Depends(authenticate)) -> dict:
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM brand WHERE id=%s", (brand_id,))
            provider = visual_generator(conn, brand_id, config)
            sdk_supported = True
            try:
                validate_image_model(provider.model)
            except DomainError:
                sdk_supported = False
            configured = provider.credentials_saved
            return {
                "credentials_saved": configured,
                "model": provider.model,
                "sdk_supported": sdk_supported,
            }

    @router.put("/visual-provider")
    def configure_provider(
        brand_id: UUID,
        data: VisualConfiguration,
        actor: Principal = Depends(authenticate),
    ) -> dict:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, {"owner", "admin"})
            validate_image_model(data.model)
            store = CredentialStore(config.credential_master_key_path)
            key = data.api_key.get_secret_value() if data.api_key else ""
            if not key:
                existing = conn.execute(
                    "SELECT 1 FROM tenant_secret WHERE brand_id=%s AND name='visual_provider'",
                    (brand_id,),
                ).fetchone()
                key = (
                    json.loads(store.read(conn, brand_id, "visual_provider"))["api_key"]
                    if existing
                    else config.gemini_api_key.get_secret_value()
                )
            if not key:
                raise DomainError("GeminiNotConfigured", "Enter your Gemini API key.", 422)
            store.write(
                conn,
                brand_id,
                "visual_provider",
                json.dumps({"api_key": key, "model": data.model}),
            )
            audit(
                conn,
                events,
                brand_id,
                "connection_change",
                "visual_provider",
                brand_id,
                {"model": data.model, "credentials_saved": True},
                "Updated image provider configuration",
            )
        return {"credentials_saved": True, "model": data.model, "sdk_supported": True}

    @router.get("/understanding")
    def understanding(brand_id: UUID, actor: Principal = Depends(authenticate)) -> dict | None:
        with db.transaction(actor) as conn:
            one(conn, "SELECT id FROM brand WHERE id=%s", (brand_id,))
            return conn.execute(
                "SELECT id,version,document,source_kind FROM brand_context "
                "WHERE brand_id=%s ORDER BY version DESC LIMIT 1",
                (brand_id,),
            ).fetchone()

    @router.put("/understanding")
    def edit_understanding(
        brand_id: UUID,
        data: ContextEdit,
        actor: Principal = Depends(authenticate),
    ) -> dict:
        with db.transaction(actor) as conn:
            locked_brand(conn, brand_id)
            require_role(conn, brand_id, EDIT_ROLES)
            previous = one(
                conn,
                "SELECT * FROM brand_context WHERE brand_id=%s ORDER BY version DESC LIMIT 1",
                (brand_id,),
            )
            if previous["version"] != data.expected_version:
                raise DomainError(
                    "RevisionConflict",
                    "Brand understanding changed. Reload before editing.",
                    409,
                )
            document = data.document.model_dump()
            if previous["document"] == document:
                return {k: previous[k] for k in ("id", "version", "document", "source_kind")}
            row = one(
                conn,
                "INSERT INTO brand_context(brand_id,version,input_hash,source_kind,document) "
                "VALUES(%s,%s,%s,'edit',%s) RETURNING id,version,document,source_kind",
                (
                    brand_id,
                    previous["version"] + 1,
                    digest({"parent": str(previous["id"]), "document": document}),
                    Jsonb(document),
                ),
            )
            audit(
                conn,
                events,
                brand_id,
                "context_edit",
                "brand_context",
                row["id"],
                {"version": row["version"]},
                "Edited brand understanding",
            )
            return row

    def draft(conn, brand_id, draft_id, revision):
        row = one(
            conn,
            "SELECT * FROM studio_draft WHERE brand_id=%s AND id=%s AND state='completed'",
            (brand_id, draft_id),
        )
        if row["revision"] != revision:
            raise DomainError(
                "RevisionConflict",
                "The creative changed. Save or reload before continuing.",
                409,
            )
        return row

    @router.post("/studio/{draft_id}/render", status_code=201)
    def render_draft(
        brand_id: UUID,
        draft_id: UUID,
        data: RenderInput,
        actor: Principal = Depends(authenticate),
    ) -> dict:
        with db.transaction(actor) as conn:
            generation_gate(conn, locked_brand(conn, brand_id))
            require_role(conn, brand_id, EDIT_ROLES)
            row = draft(conn, brand_id, draft_id, data.expected_revision)
            result = persist_render(conn, storage, brand_id, row, data.aspect_ratio)
            audit(
                conn,
                events,
                brand_id,
                "creative_render",
                "studio_draft",
                draft_id,
                {"aspect_ratio": data.aspect_ratio, "revision": data.expected_revision},
                "Rendered finished ad",
            )
        return {
            "id": str(result["id"]),
            "width": result["width"],
            "height": result["height"],
            "scene_graph": result["scene_graph"],
            "png_url": f"/api/brands/{brand_id}/renditions/{result['id']}.png",
            "svg_url": f"/api/brands/{brand_id}/renditions/{result['id']}.svg",
        }

    @router.get("/renditions/{rendition_id}.{extension}")
    def download(
        brand_id: UUID,
        rendition_id: UUID,
        extension: Literal["png", "svg"],
        actor: Principal = Depends(authenticate),
    ):
        with db.transaction(actor) as conn:
            row = one(
                conn,
                "SELECT * FROM studio_rendition WHERE brand_id=%s AND id=%s",
                (brand_id, rendition_id),
            )
        return Response(
            storage.read(brand_id, row[f"{extension}_key"]),
            media_type="image/png" if extension == "png" else "image/svg+xml",
            headers={
                "Content-Disposition": f'attachment; filename="ad-{rendition_id}.{extension}"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.post("/studio/{draft_id}/attach", status_code=201)
    def attach(
        brand_id: UUID,
        draft_id: UUID,
        data: AttachInput,
        actor: Principal = Depends(authenticate),
    ) -> dict:
        with db.transaction(actor) as conn:
            generation_gate(conn, locked_brand(conn, brand_id))
            require_role(conn, brand_id, EDIT_ROLES)
            row = draft(conn, brand_id, draft_id, data.expected_revision)
            plan = one(
                conn,
                "SELECT id,state FROM plan WHERE brand_id=%s AND id=%s",
                (brand_id, data.plan_id),
            )
            if plan["state"] != "draft":
                raise DomainError(
                    "PlanNotEditable",
                    "Attach creative to a draft plan before activation.",
                    409,
                )
            existing = conn.execute(
                "SELECT creative_id FROM studio_plan_creative WHERE brand_id=%s "
                "AND draft_id=%s AND draft_revision=%s AND plan_id=%s",
                (brand_id, draft_id, data.expected_revision, data.plan_id),
            ).fetchone()
            specs = conn.execute(
                "SELECT DISTINCT ON(s.channel,s.placement_key) s.* FROM placement_spec s "
                "JOIN plan_allocation a ON a.channel=s.channel WHERE a.plan_id=%s "
                "AND s.format='static_image' AND s.retired_at IS NULL "
                "ORDER BY s.channel,s.placement_key,s.registry_version DESC",
                (data.plan_id,),
            ).fetchall()
            rendered = {
                ratio: persist_render(conn, storage, brand_id, row, ratio) for ratio in SIZES
            }
            for spec in specs:
                ratio = spec["aspect_ratio"]
                if ratio not in SIZES:
                    raise DomainError(
                        "UnsupportedPlacement",
                        f"Unsupported placement ratio: {ratio}.",
                        422,
                    )
                rendered[ratio] = persist_render(conn, storage, brand_id, row, ratio)
                result = rendered[ratio]
                for layer in result["scene_graph"]["layers"]:
                    if layer["type"] != "text":
                        continue
                    insets = spec["safe_zone_insets"]
                    if (
                        layer["x"] < insets.get("left", 0)
                        or layer["y"] < insets.get("top", 0)
                        or layer["x"] + layer["width"] > result["width"] - insets.get("right", 0)
                        or layer["y"] + layer["height"] > result["height"] - insets.get("bottom", 0)
                    ):
                        raise DomainError(
                            "PlacementSafeArea",
                            f"Text exceeds {spec['placement_key']} safe area.",
                            422,
                        )
                if (
                    result["width"] < spec["min_width_px"]
                    or result["height"] < spec["min_height_px"]
                ):
                    raise DomainError(
                        "PlacementDimensions",
                        f"Image is too small for {spec['placement_key']}.",
                        422,
                    )
                for field, limit in spec["text_limits"].items():
                    key = "primary_text" if field == "primary" else field
                    if key in row["document"]["meta"] and len(row["document"]["meta"][key]) > int(
                        limit
                    ):
                        raise DomainError(
                            "PlacementTextLimit",
                            f"{spec['placement_key']}: {key} exceeds {limit} characters.",
                            422,
                        )
            if existing:
                creative = {"id": existing["creative_id"]}
            else:
                concept = one(
                    conn,
                    "INSERT INTO creative_concept(plan_id,brand_id,name,hypothesis,brief) "
                    "VALUES(%s,%s,%s,%s,%s) RETURNING id",
                    (
                        data.plan_id,
                        brand_id,
                        row["document"]["meta"]["headline"],
                        row["document"].get("concept", {}).get("hypothesis")
                        or "Test the saved creative against the campaign objective.",
                        Jsonb(row["document"]),
                    ),
                )
                creative = one(
                    conn,
                    "INSERT INTO creative(concept_id,brand_id,format,state,scene_graph,"
                    "creative_hash,copy_fields) "
                    "VALUES(%s,%s,'static_image','rendered',%s,%s,%s) RETURNING id",
                    (
                        concept["id"],
                        brand_id,
                        Jsonb(row["scene_graph"]),
                        digest(row["scene_graph"]),
                        Jsonb(row["document"]),
                    ),
                )
            for spec in specs:
                if conn.execute(
                    "SELECT 1 FROM rendition WHERE creative_id=%s AND placement_spec_id=%s",
                    (creative["id"], spec["id"]),
                ).fetchone():
                    continue
                result = rendered[spec["aspect_ratio"]]
                content = storage.read(brand_id, result["png_key"])
                if spec["max_bytes"] and len(content) > spec["max_bytes"]:
                    raise DomainError(
                        "PlacementFileSize",
                        f"Rendered file exceeds {spec['placement_key']} limits.",
                        422,
                    )
                asset = one(
                    conn,
                    "INSERT INTO asset(brand_id,role,storage_uri,content_hash,mime_type,"
                    "bytes,width_px,height_px) "
                    "VALUES(%s,'rendition',%s,%s,'image/png',%s,%s,%s) "
                    "ON CONFLICT(brand_id,content_hash,role) DO UPDATE SET "
                    "content_hash=excluded.content_hash RETURNING id",
                    (
                        brand_id,
                        result["png_key"],
                        result["png_key"],
                        len(content),
                        result["width"],
                        result["height"],
                    ),
                )
                conn.execute(
                    "INSERT INTO rendition(creative_id,brand_id,channel,placement_spec_id,"
                    "asset_id,render_cache_key,"
                    "spec_validation_passed,spec_validation_detail,"
                    "text_contrast_ratio,rendered_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,true,%s,%s,now())",
                    (
                        creative["id"],
                        brand_id,
                        spec["channel"],
                        spec["id"],
                        asset["id"],
                        result["png_key"],
                        Jsonb(
                            {
                                "scene_graph": result["scene_graph"],
                                "studio_revision": data.expected_revision,
                            }
                        ),
                        result["scene_graph"]["text_contrast_ratio"],
                    ),
                )
            conn.execute(
                "INSERT INTO studio_plan_creative VALUES(%s,%s,%s,%s,%s,now()) "
                "ON CONFLICT DO NOTHING",
                (
                    brand_id,
                    draft_id,
                    data.expected_revision,
                    data.plan_id,
                    creative["id"],
                ),
            )
            audit(
                conn,
                events,
                brand_id,
                "creative_render",
                "creative",
                creative["id"],
                {"plan_id": str(data.plan_id), "draft_id": str(draft_id)},
                "Attached rendered creative to campaign plan",
            )
            return {"creative_id": str(creative["id"]), "plan_id": str(data.plan_id)}

    return router
