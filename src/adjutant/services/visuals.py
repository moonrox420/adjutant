"""Google image generation without embedded ad copy or fabricated image fallbacks."""

import base64
import io
import os
import re
import warnings

import httpx
from google import genai
from google.genai import errors, types
from PIL import Image, UnidentifiedImageError

from adjutant.errors import DomainError

DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"


def validate_image_model(model: str) -> None:
    """Reject retired Imagen and non-image endpoints before accepting configuration."""
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]*image[a-zA-Z0-9.-]*", model):
        raise DomainError(
            "UnsupportedImageModel",
            "Choose a Gemini image model, such as gemini-3.1-flash-image. "
            "Imagen models are retired in the Gemini API.",
            422,
        )


class VisualsGenerator:
    """Generate image-only backgrounds; copy remains editable text in the scene graph."""

    def __init__(
        self, api_key: str | None = None, *, model: str = DEFAULT_IMAGE_MODEL
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        )
        self.model = model

    @property
    def credentials_saved(self) -> bool:
        return bool(self._api_key)

    def require_configuration(self) -> None:
        if not self._api_key:
            raise DomainError(
                "GeminiNotConfigured",
                "Set GEMINI_API_KEY in the server .env file and restart the API to generate "
                "images.",
                503,
            )
        validate_image_model(self.model)

    async def generate_ad_image(self, prompt: str, aspect_ratio: str = "1:1") -> str:
        self.require_configuration()
        if aspect_ratio not in {"1:1", "4:5", "9:16", "16:9", "3:4", "4:3"}:
            raise DomainError(
                "InvalidAspectRatio", "Unsupported image aspect ratio.", 422
            )
        if not prompt.strip() or len(prompt) > 4000:
            raise DomainError(
                "InvalidVisualPrompt",
                "A visual prompt of 1–4000 characters is required.",
                422,
            )
        image_prompt = (
            "Create a professional commercial advertising photograph or illustration. "
            "This is the BACKGROUND IMAGE ONLY: no text, letters, captions, logos, "
            "watermarks, CTA buttons, or invented certifications. Ad copy is added separately "
            "as editable text. Follow this visual description: " + prompt
        )
        try:
            async with genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(
                    timeout=120000, retry_options=types.HttpRetryOptions(attempts=1)
                ),
            ).aio as client:
                response = await client.models.generate_content(
                    model=self.model,
                    contents=image_prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                    ),
                )
                for candidate in response.candidates or []:
                    for part in (
                        (candidate.content.parts or []) if candidate.content else []
                    ):
                        blob = part.inline_data
                        if blob and blob.data:
                            return self.data_uri(
                                blob.data, blob.mime_type or "image/jpeg"
                            )
        except ValueError as exc:
            raise DomainError(
                "ImageModelUnavailable",
                "The installed Google SDK cannot use this image model with GEMINI_API_KEY. "
                "Set ADJUTANT_GEMINI_IMAGE_MODEL to a supported Gemini image model and restart.",
                503,
            ) from exc
        except errors.APIError as exc:
            if exc.code == 404:
                raise DomainError(
                    "ImageModelUnavailable",
                    "Google does not provide the configured image model. Set "
                    "ADJUTANT_GEMINI_IMAGE_MODEL to a supported Gemini image model and "
                    "restart the API.",
                    503,
                ) from exc
            if exc.code in {401, 403}:
                message = (
                    "Google denied image generation. Check GEMINI_API_KEY and project "
                    "billing/access."
                )
            elif exc.code == 429:
                message = (
                    "Google image generation quota was reached. "
                    "Retry after your quota resets."
                )
            else:
                message = (
                    "Google could not generate the image. Check the model and project "
                    "configuration."
                )
            raise DomainError(
                "ImageGenerationFailed", message, 429 if exc.code == 429 else 502
            ) from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise DomainError(
                "ImageGenerationUnavailable",
                "Google image generation timed out or could not connect. Retry shortly.",
                503,
            ) from exc
        raise DomainError(
            "ImageGenerationBlocked",
            "Google returned no image, possibly because of its content policy. Revise the "
            "brief and retry.",
            422,
        )

    @staticmethod
    def data_uri(content: bytes, mime: str | None) -> str:
        mime = mime or "image/jpeg"
        if (
            mime not in {"image/png", "image/jpeg", "image/webp"}
            or len(content) > 20 * 1024 * 1024
        ):
            raise DomainError(
                "InvalidGeneratedImage", "Google returned an unsupported image.", 502
            )
        signatures = {
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
        }
        if not signatures[mime]:
            raise DomainError(
                "InvalidGeneratedImage", "Google returned invalid image bytes.", 502
            )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as decoded:
                    if decoded.width * decoded.height > 25_000_000:
                        raise ValueError("Image exceeds pixel limit")
                    if (
                        Image.MIME.get(decoded.format or "") != mime
                        or getattr(decoded, "is_animated", False)
                    ):
                        raise ValueError("Image format does not match response")
                    decoded.verify()
                with Image.open(io.BytesIO(content)) as decoded:
                    decoded.load()
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            SyntaxError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise DomainError(
                "InvalidGeneratedImage",
                "Google returned an image that cannot be decoded.",
                502,
            ) from exc
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
