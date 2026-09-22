"""Creative directions and duplicate checks shared by generation and durable Studio jobs."""

import re
import unicodedata

from adjutant.errors import DomainError
from adjutant.studio_models import AdCopyBundle

CONCEPT_DIRECTIONS = (
    {
        "name": "The problem",
        "hypothesis": "A recognizable customer problem makes the offer immediately relevant.",
        "brief": "Show the customer's problem in its real setting; lead with recognition.",
    },
    {
        "name": "The outcome",
        "hypothesis": "Showing the desired everyday outcome increases qualified interest.",
        "brief": "Show the positive everyday outcome, without inventing performance guarantees.",
    },
    {
        "name": "How it works",
        "hypothesis": "A concrete process demonstration reduces uncertainty about the offer.",
        "brief": "Show the product or service in use with a close-up process demonstration.",
    },
    {
        "name": "The moment",
        "hypothesis": "A specific occasion helps the audience recognize when they need the offer.",
        "brief": "Show a specific customer occasion or use case, with a story-led composition.",
    },
    {
        "name": "The offer",
        "hypothesis": "A clear explanation of the actual offer makes the next step easier.",
        "brief": "Make the product or service the visual focus; explain the factual offer simply.",
    },
)


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def distinct_concept(bundle: AdCopyBundle, previous: list[dict]) -> None:
    """Reject identical copy or substantially repeated visual descriptions before image spend."""
    headline = words(bundle.meta.headline)
    visual = words(bundle.meta.image_prompt)
    for document in previous:
        prior = document["meta"]
        prior_visual = words(prior["image_prompt"])
        similarity = len(visual & prior_visual) / max(1, len(visual | prior_visual))
        if headline == words(prior["headline"]) or similarity >= 0.8:
            raise DomainError(
                "ConceptsTooSimilar",
                "The copy model repeated an earlier concept. Retry with a more specific brief; "
                "each concept needs a different message and visual composition.",
                422,
            )
