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


class YouTubeCopy(Input):
    headline: str = Field(default="Watch Now", min_length=1, max_length=30)
    long_headline: str = Field(
        default="Experience the difference today", min_length=1, max_length=90
    )
    description: str = Field(
        default="Discover more and get started now.", min_length=1, max_length=90
    )
    cta: str = Field(default="Learn More", min_length=1, max_length=40)


class LinkedInCopy(Input):
    introductory_text: str = Field(
        default="Discover cutting-edge solutions for your business.", min_length=1, max_length=600
    )
    headline: str = Field(default="Transform Your Workflow", min_length=1, max_length=70)
    cta: str = Field(default="Learn More", min_length=1, max_length=40)


class RedditCopy(Input):
    post_title: str = Field(
        default="What every team should know before scaling", min_length=1, max_length=300
    )
    cta: str = Field(default="Sign Up", min_length=1, max_length=40)


class AdCopyBundle(Input):
    understanding: BrandUnderstanding
    meta: MetaCopy
    google: GoogleCopy
    youtube: YouTubeCopy = Field(default_factory=lambda: YouTubeCopy())
    linkedin: LinkedInCopy = Field(default_factory=lambda: LinkedInCopy())
    reddit: RedditCopy = Field(default_factory=lambda: RedditCopy())


# Section 8.2 Schema aliases and bundles
GoogleAdsCopy = GoogleCopy


class CreativeCopyBundle(Input):
    concept_id: str
    meta: MetaCopy
    google_ads: GoogleAdsCopy
    youtube: YouTubeCopy
    linkedin: LinkedInCopy
    reddit: RedditCopy


class QuickGenerateRequest(Input):
    brand_id: UUID
    url_or_prompt: str = Field(min_length=3, max_length=10000)


class DraftEdit(Input):
    expected_revision: int = Field(ge=1)
    brand_name: str = Field(min_length=2, max_length=120)
    destination_url: HttpUrl
    meta: MetaCopy
    google: GoogleCopy
    youtube: YouTubeCopy = Field(default_factory=lambda: YouTubeCopy())
    linkedin: LinkedInCopy = Field(default_factory=lambda: LinkedInCopy())
    reddit: RedditCopy = Field(default_factory=lambda: RedditCopy())
