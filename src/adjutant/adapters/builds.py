"""Construction registry keeps platform dispatch below the adapter boundary."""

from adjutant.adapters.google_ads_build import (
    GoogleAdsBuilder,
    GoogleAdsBuildSettings,
)
from adjutant.adapters.google_ads_build import (
    preflight as google_ads_preflight,
)
from adjutant.adapters.linkedin_build import (
    LinkedInBuilder,
    LinkedInBuildSettings,
)
from adjutant.adapters.linkedin_build import (
    preflight as linkedin_preflight,
)
from adjutant.adapters.meta_build import MetaBuilder, MetaBuildSettings
from adjutant.adapters.meta_build import preflight as meta_preflight
from adjutant.adapters.reddit_build import (
    RedditBuilder,
    RedditBuildSettings,
)
from adjutant.adapters.reddit_build import (
    preflight as reddit_preflight,
)
from adjutant.adapters.youtube_build import (
    YouTubeBuilder,
    YouTubeBuildSettings,
)
from adjutant.adapters.youtube_build import (
    preflight as youtube_preflight,
)
from adjutant.errors import DomainError

BUILDERS = {
    "meta": (MetaBuilder, MetaBuildSettings, meta_preflight),
    "google_ads": (GoogleAdsBuilder, GoogleAdsBuildSettings, google_ads_preflight),
    "youtube": (YouTubeBuilder, YouTubeBuildSettings, youtube_preflight),
    "linkedin": (LinkedInBuilder, LinkedInBuildSettings, linkedin_preflight),
    "reddit": (RedditBuilder, RedditBuildSettings, reddit_preflight),
}


def build_configuration(channel: str) -> dict | None:
    entry = BUILDERS.get(channel)
    if entry is None:
        return None
    schema = entry[1].model_json_schema()
    fields = []
    for name, raw in schema["properties"].items():
        definition = next(
            (item for item in raw.get("anyOf", []) if item.get("type") != "null"), raw
        )
        fields.append(
            {
                "name": name,
                "label": raw.get("title", name.replace("_", " ")),
                "type": definition.get("type", "string"),
                "format": definition.get("format"),
                "required": name in schema.get("required", []),
                "default": raw.get("default"),
                "minimum": definition.get("minimum"),
                "maximum": definition.get("maximum"),
                "pattern": definition.get("pattern"),
            }
        )
    return {"fields": fields}


def builder_for(channel: str):
    """Return an executable builder, never an interface-only capability claim."""
    entry = BUILDERS.get(channel)
    if entry is None:
        raise DomainError(
            "CampaignBuilderUnavailable",
            "This channel does not yet have a campaign construction path.",
            409,
        )
    return entry
