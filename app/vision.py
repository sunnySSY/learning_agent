"""Image validation and the replaceable structured Vision adapter."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from config import cfg


IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}


@dataclass(frozen=True)
class ImageInfo:
    data: bytes
    media_type: str
    width: int
    height: int


def preprocess_image(data: bytes, media_type: str) -> ImageInfo:
    if media_type not in IMAGE_TYPES:
        raise ValueError("unsupported_image_type")
    if len(data) > cfg.image_max_bytes:
        raise ValueError("image_too_large")
    signature = MAGIC[media_type]
    if not data.startswith(signature):
        raise ValueError("malformed_image")
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
            if max(width, height) > cfg.image_max_edge:
                raise ValueError("image_dimensions_exceeded")
            # Re-encode to remove EXIF/GPS and normalise the provider input.
            image = image.convert("RGB")
            output = io.BytesIO(); image.save(output, format="PNG", optimize=True)
            return ImageInfo(output.getvalue(), "image/png", width, height)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError("malformed_image") from exc


@dataclass(frozen=True)
class VisionResult:
    recognized_text: str
    formulas: list[dict]
    uncertain_regions: list[dict]
    confidence: float
    requires_confirmation: bool

    @property
    def context(self) -> dict:
        return {
            "recognized_text": self.recognized_text,
            "formulas": self.formulas,
            "uncertain_regions": self.uncertain_regions,
            "confidence": self.confidence,
        }


class VisionService:
    """Call the configured chat model for structured recognition.

    The adapter intentionally never returns an answer to the question.  It
    produces only recognition context; the normal LangGraph solver runs after
    the user confirms uncertain fields.
    """

    def parse(self, images: list[ImageInfo]) -> VisionResult:
        if not images:
            raise ValueError("no_images")
        try:
            from pydantic import BaseModel, Field
            from llm import chat_model

            class Parse(BaseModel):
                recognized_text: str = ""
                formulas: list[dict] = Field(default_factory=list)
                uncertain_regions: list[dict] = Field(default_factory=list)
                confidence: float = 0.0

            content: list[dict] = [{"type": "text", "text": "识别题目文字、公式和关键数字。只输出结构化识别结果，不要解题；无法确认的区域放入 uncertain_regions。"}]
            for image in images:
                content.append({"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{base64.b64encode(image.data).decode('ascii')}", "detail": "high"}})
            response = chat_model().with_structured_output(Parse, method="json_schema").invoke([
                {"role": "system", "content": "你是图像文字识别器。不要猜测模糊数字或公式。"},
                {"role": "user", "content": content},
            ])
            parsed = response if isinstance(response, Parse) else Parse.model_validate(response)
            confidence = max(0.0, min(1.0, float(parsed.confidence)))
            uncertain = list(parsed.uncertain_regions or [])
            return VisionResult(parsed.recognized_text, list(parsed.formulas or []), uncertain, confidence, bool(uncertain or confidence < 0.8))
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("provider_unavailable") from exc


def context_text(context: dict) -> str:
    """Render confirmed recognition into solver input, without image bytes."""
    lines = [context.get("recognized_text", "")]
    for formula in context.get("formulas") or []:
        if formula.get("latex"):
            lines.append(f"公式：{formula['latex']}")
    return "\n".join(line for line in lines if line)
