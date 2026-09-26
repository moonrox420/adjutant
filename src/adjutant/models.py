from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    WithJsonSchema,
    field_validator,
    model_validator,
)

Money = Annotated[
    Decimal,
    Field(gt=0, max_digits=12, decimal_places=2),
    # Ollama's grammar converter does not support Pydantic's decimal lookahead regex.
    WithJsonSchema(
        {"type": "string", "pattern": r"^[0-9]{1,10}\.[0-9]{2}$"}, mode="serialization"
    ),
]
Channel = Literal[
    "meta",
    "google_ads",
    "youtube",
    "tiktok",
    "linkedin",
    "microsoft",
    "reddit",
    "pinterest",
    "snapchat",
    "amazon_ads",
]
Objective = Literal[
    "awareness",
    "traffic",
    "engagement",
    "leads",
    "app_promotion",
    "sales",
    "store_visits",
    "video_views",
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class BrandInput(Input):
    account_id: UUID
    display_name: str = Field(min_length=2, max_length=120)
    website_url: HttpUrl
    vertical: Literal[
        "home_services",
        "retail",
        "ecommerce",
        "hospitality",
        "professional_services",
        "education",
        "other",
        "political",
        "pharmaceutical",
        "gambling",
        "crypto",
        "financial_income_claims",
    ] = "other"
    monthly_ceiling: Money
    daily_ceiling: Money


class AssertionInput(Input):
    field_path: str = Field(
        min_length=2, max_length=200, pattern=r"^[a-zA-Z0-9_.\[\]-]+$"
    )
    value: str = Field(min_length=1, max_length=4000)
    provenance_uri: HttpUrl | None = None
    is_claim: bool = False


class Allocation(Input):
    channel: Channel
    monthly_budget_usd: Money
    daily_budget_usd: Money


class PlanInput(Input):
    name: str = Field(min_length=3, max_length=140)
    objective: Objective = "leads"
    goal_kind: Literal[
        "target_cpa", "target_roas", "lead_volume", "efficient_spend"
    ] = "target_cpa"
    goal_value: Money
    monthly_budget_usd: Money
    rationale: str = Field(min_length=20, max_length=8000)
    audience: str = Field(min_length=5, max_length=3000)
    hypothesis: str = Field(min_length=10, max_length=3000)
    allocations: list[Allocation] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def balanced(self) -> Self:
        channels = [a.channel for a in self.allocations]
        if len(channels) != len(set(channels)):
            raise ValueError("Each channel must appear exactly once")
        if (
            sum(a.monthly_budget_usd for a in self.allocations)
            != self.monthly_budget_usd
        ):
            raise ValueError(
                "Channel allocations must sum exactly to the monthly budget"
            )
        return self


class PlanEdit(PlanInput):
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApprovalInput(Input):
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    requires_client_approval: bool = False


class Decision(Input):
    decision: Literal["approved", "rejected", "changes_requested"]
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def rejection_reason(self) -> Self:
        if self.decision != "approved" and len(self.reason) < 10:
            raise ValueError(
                "Explain the requested change or rejection in at least 10 characters"
            )
        return self


class KillInput(Input):
    reason: str = Field(min_length=10, max_length=1000)


class CeilingInput(Input):
    monthly_ceiling: Money
    daily_ceiling: Money


class GenerateInput(Input):
    provider: Literal["local", "cloud"] = "local"
    model: str = Field(min_length=1, max_length=200)
    brief: str = Field(min_length=10, max_length=3000)
