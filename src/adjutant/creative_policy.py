"""Brand claim validation shared by copy editing, generation, and rendering."""

import unicodedata
from uuid import UUID

from adjutant.errors import DomainError


def enforce_blocked_claims(conn, brand_id: UUID, document: dict) -> None:
    def normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    text = [normalize(value) for value in strings(document)]
    for row in conn.execute(
        "SELECT value FROM brand_constraint WHERE brand_id=%s AND is_active "
        "AND kind IN ('banned_word','banned_claim') UNION "
        "SELECT unnest(blocked_claims) AS value FROM guardrail WHERE brand_id=%s",
        (brand_id, brand_id),
    ).fetchall():
        phrase = normalize(row["value"])
        if phrase and any(phrase in value for value in text):
            raise DomainError(
                "BlockedClaim",
                "The copy contains a phrase blocked by this brand. Revise the brief or copy.",
                422,
            )
