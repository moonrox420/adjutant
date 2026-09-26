"""Video generation, timeline of scene graphs, safe area enforcement,
hook variants, and fallback.
"""

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection

from adjutant.errors import DomainError


@dataclass(frozen=True)
class SafeArea:
    top_pct: float
    bottom_pct: float
    left_pct: float
    right_pct: float


# Safe areas account for overlay chrome per channel format
SAFE_AREAS = {
    "9:16": {
        "tiktok": SafeArea(top_pct=0.10, bottom_pct=0.20, left_pct=0.05, right_pct=0.15),
        "meta": SafeArea(top_pct=0.12, bottom_pct=0.18, left_pct=0.06, right_pct=0.06),
        "snapchat": SafeArea(top_pct=0.10, bottom_pct=0.15, left_pct=0.05, right_pct=0.05),
        "youtube": SafeArea(top_pct=0.08, bottom_pct=0.16, left_pct=0.05, right_pct=0.12),
        "default": SafeArea(top_pct=0.10, bottom_pct=0.18, left_pct=0.05, right_pct=0.10),
    },
    "1:1": {
        "meta": SafeArea(top_pct=0.05, bottom_pct=0.05, left_pct=0.05, right_pct=0.05),
        "linkedin": SafeArea(top_pct=0.05, bottom_pct=0.05, left_pct=0.05, right_pct=0.05),
        "default": SafeArea(top_pct=0.05, bottom_pct=0.05, left_pct=0.05, right_pct=0.05),
    },
    "16:9": {
        "youtube": SafeArea(top_pct=0.08, bottom_pct=0.10, left_pct=0.08, right_pct=0.08),
        "google_ads": SafeArea(top_pct=0.08, bottom_pct=0.10, left_pct=0.08, right_pct=0.08),
        "default": SafeArea(top_pct=0.08, bottom_pct=0.10, left_pct=0.08, right_pct=0.08),
    },
}


@dataclass
class VideoScene:
    id: str
    duration_seconds: float
    scene_graph: dict[str, Any]
    captions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VideoTimeline:
    concept_id: UUID
    aspect_ratio: str  # 9:16, 1:1, 16:9
    hook_scene: VideoScene
    body_scenes: list[VideoScene]
    cta_scene: VideoScene

    @property
    def total_duration(self) -> float:
        return (
            self.hook_scene.duration_seconds
            + sum(s.duration_seconds for s in self.body_scenes)
            + self.cta_scene.duration_seconds
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": str(self.concept_id),
            "aspect_ratio": self.aspect_ratio,
            "total_duration": self.total_duration,
            "hook_scene": {
                "id": self.hook_scene.id,
                "duration_seconds": self.hook_scene.duration_seconds,
                "scene_graph": self.hook_scene.scene_graph,
                "captions": self.hook_scene.captions,
            },
            "body_scenes": [
                {
                    "id": s.id,
                    "duration_seconds": s.duration_seconds,
                    "scene_graph": s.scene_graph,
                    "captions": s.captions,
                }
                for s in self.body_scenes
            ],
            "cta_scene": {
                "id": self.cta_scene.id,
                "duration_seconds": self.cta_scene.duration_seconds,
                "scene_graph": self.cta_scene.scene_graph,
                "captions": self.cta_scene.captions,
            },
        }


def validate_safe_areas(scene_graph: dict[str, Any], aspect_ratio: str, channel: str) -> list[str]:
    """Validate that text and CTA layers fall strictly inside the channel-specific safe area."""
    ratio_rules = SAFE_AREAS.get(aspect_ratio, {})
    safe_area = (
        ratio_rules.get(channel) or ratio_rules.get("default") or SafeArea(0.05, 0.05, 0.05, 0.05)
    )

    violations = []
    layers = scene_graph.get("layers", [])
    for layer in layers:
        layer_type = layer.get("type", "")
        if layer_type in {"text", "cta_button", "headline"}:
            pos = layer.get("position", {})
            top = pos.get("top_pct", 0.0)
            bottom = pos.get("bottom_pct", 0.0)
            left = pos.get("left_pct", 0.0)
            right = pos.get("right_pct", 0.0)

            if top < safe_area.top_pct:
                violations.append(
                    f"Layer '{layer.get('id', 'unnamed')}' top {top:.2f} "
                    f"breaches safe area top bound {safe_area.top_pct:.2f}"
                )
            if bottom < safe_area.bottom_pct:
                violations.append(
                    f"Layer '{layer.get('id', 'unnamed')}' bottom {bottom:.2f} "
                    f"breaches safe area bottom bound {safe_area.bottom_pct:.2f}"
                )
            if left < safe_area.left_pct:
                violations.append(
                    f"Layer '{layer.get('id', 'unnamed')}' left {left:.2f} "
                    f"breaches safe area left bound {safe_area.left_pct:.2f}"
                )
            if right < safe_area.right_pct:
                violations.append(
                    f"Layer '{layer.get('id', 'unnamed')}' right {right:.2f} "
                    f"breaches safe area right bound {safe_area.right_pct:.2f}"
                )

    return violations


def generate_hook_variants(
    base_timeline: VideoTimeline, hook_copy_variants: list[dict[str, Any]]
) -> list[VideoTimeline]:
    """Generate 3 hook variants from one concept without re-rendering or altering body scenes."""
    variants = []
    for i, hook_copy in enumerate(hook_copy_variants[:3], start=1):
        modified_scene_graph = dict(base_timeline.hook_scene.scene_graph)
        modified_scene_graph["layers"] = [
            (
                {
                    **layer,
                    "content": hook_copy.get(layer.get("field", "headline"), layer.get("content")),
                }
                if layer.get("type") in {"headline", "text"}
                else layer
            )
            for layer in modified_scene_graph.get("layers", [])
        ]
        hook_scene = VideoScene(
            id=f"hook_variant_{i}",
            duration_seconds=base_timeline.hook_scene.duration_seconds,
            scene_graph=modified_scene_graph,
            captions=hook_copy.get("captions", base_timeline.hook_scene.captions),
        )
        variants.append(
            VideoTimeline(
                concept_id=base_timeline.concept_id,
                aspect_ratio=base_timeline.aspect_ratio,
                hook_scene=hook_scene,
                body_scenes=base_timeline.body_scenes,
                cta_scene=base_timeline.cta_scene,
            )
        )
    return variants


def render_video_timeline(
    conn: Connection[Any],
    brand_id: UUID,
    timeline: VideoTimeline,
    channel: str,
    static_fallback_creative_id: UUID | None = None,
    simulate_render_failure: bool = False,
) -> dict[str, Any]:
    """Validate all timeline scenes and handle degradation to static fallback.

    Direct MP4 video synthesis raises 501 NotImplemented unless a static fallback is used.
    """
    start_time = time.perf_counter()

    # Validate safe areas across all timeline scenes (hook, body scenes, CTA)
    violations = []
    violations.extend(
        validate_safe_areas(timeline.hook_scene.scene_graph, timeline.aspect_ratio, channel)
    )
    for body_scene in timeline.body_scenes:
        violations.extend(
            validate_safe_areas(body_scene.scene_graph, timeline.aspect_ratio, channel)
        )
    violations.extend(
        validate_safe_areas(timeline.cta_scene.scene_graph, timeline.aspect_ratio, channel)
    )

    if violations:
        raise DomainError(
            "SafeAreaViolation",
            f"Video scene graph violates safe area bounds for {channel} "
            f"at {timeline.aspect_ratio}: {'; '.join(violations)}",
            422,
        )

    video_id = uuid4()
    if simulate_render_failure or static_fallback_creative_id is not None:
        # S11.3: Graceful degradation to static creative
        elapsed_sec = time.perf_counter() - start_time
        conn.execute(
            """INSERT INTO video_render_log(
                id, brand_id, concept_id, aspect_ratio, channel, status,
                duration_seconds, cost_usd, fallback_creative_id, error_detail
            ) VALUES (%s, %s, %s, %s, %s, 'degraded_to_static', %s, 0.00, %s, %s)""",
            (
                video_id,
                brand_id,
                timeline.concept_id,
                timeline.aspect_ratio,
                channel,
                elapsed_sec,
                static_fallback_creative_id,
                "Render pipeline degraded to verified static fallback creative",
            ),
        )
        return {
            "status": "degraded_to_static",
            "creative_id": (
                str(static_fallback_creative_id) if static_fallback_creative_id else None
            ),
            "fallback_used": True,
            "reason": "Video render pipeline degraded to static asset with zero dark-time",
            "aspect_ratio": timeline.aspect_ratio,
            "render_duration_sec": elapsed_sec,
        }

    raise DomainError(
        "NotImplemented",
        "Direct MP4 video synthesis is not implemented; "
        "use static image fallbacks and storyboard scripts.",
        501,
    )
