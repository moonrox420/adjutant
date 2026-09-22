"""Typed editable ad-copy and automatically accepted brand-context documents."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, HttpUrl

from adjutant.models import Input


class BrandUnderstanding(Input):
    offers: list[str] = Field(max_length=12)
    audience: str = Field(min_length=1, max_length=1000)
    voice: str = Field(min_length=1, max_length=500)
    proof_points: list[str] = Field(max_length=12)


class MetaCopy(Input):
    headline: str = Field(min_length=1, max_length=40)
    primary_text: str = Field(min_length=1, max_length=2000)
    description: str = Field(min_length=1, max_length=100)
    cta: str = Field(min_length=1, max_length=40)
    image_prompt: str = Field(min_length=10, max_length=2000)


class GoogleCopy(Input):
    headlines: list[Annotated[str, Field(min_length=1, max_length=30)]] = Field(
        min_length=3, max_length=15
    )
    descriptions: list[Annotated[str, Field(min_length=1, max_length=90)]] = Field(
        min_length=2, max_length=4
    )
    destination_path: str = Field(max_length=31)


class TikTokCopy(Input):
    hook: str = Field(min_length=1, max_length=150)
    visual_script: str = Field(min_length=1, max_length=2000)
    cta: str = Field(min_length=1, max_length=60)


class AdCopyBundle(Input):
    understanding: BrandUnderstanding
    meta: MetaCopy
    google: GoogleCopy
    tiktok: TikTokCopy


class QuickGenerateRequest(Input):
    brand_id: UUID
    url_or_prompt: str = Field(min_length=3, max_length=10000)


class DraftEdit(Input):
    expected_revision: int = Field(ge=1)
    brand_name: str = Field(min_length=2, max_length=120)
    destination_url: HttpUrl
    meta: MetaCopy
    google: GoogleCopy
    tiktok: TikTokCopy
