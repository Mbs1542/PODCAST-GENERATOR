"""Illustrations for the gallery, made by a Gemini image model from the episode plan."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
STYLES = {
    "illustration": "clean modern editorial illustration, soft gradients, gentle lighting",
    "photo": "realistic documentary photograph, natural light, shallow depth of field",
    "3d": "soft 3D render, clay-like materials, studio lighting",
    "watercolor": "loose watercolor illustration on textured paper",
}


class ImageGenError(RuntimeError):
    """A message written for the user."""


def image_prompts(plan: Dict[str, Any], title: str, count: int, style: str = "illustration") -> List[str]:
    """One scene per body segment (cycled if more images are asked for), never with text in it."""
    look = STYLES.get(style, STYLES["illustration"])
    segments = [s for s in plan.get("segments", []) if s.get("kind") == "body"] or plan.get("segments", [])
    scenes: List[str] = []
    for seg in segments:
        concepts = ", ".join(seg.get("concepts", [])[:3])
        scenes.append(" - ".join(x for x in (seg.get("title", ""), seg.get("goal", ""), concepts) if x))
    if not scenes:
        scenes = [title]
    prompts = []
    for i in range(max(1, count)):
        scene = scenes[i % len(scenes)]
        variation = "" if i < len(scenes) else " Show a different moment or angle of the same idea."
        prompts.append(
            f"Wide 16:9 image for an educational podcast episode titled \"{plan.get('title') or title}\". "
            f"It should evoke: {scene}.{variation} Style: {look}. Leave calm space in the lower third. "
            "Absolutely no text, letters, numbers, captions, watermarks or logos anywhere in the image."
        )
    return prompts


class GeminiImageMaker:
    def __init__(self, api_key: str, model: str = DEFAULT_IMAGE_MODEL, client: Any = None) -> None:
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self.client = client
        self.model = model or DEFAULT_IMAGE_MODEL

    def make(self, prompt: str) -> Tuple[bytes, str]:
        """Return (image bytes, file suffix)."""
        from google.genai import types

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="16:9"),
                ),
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == 429:
                raise ImageGenError("Gemini הגביל את קצב יצירת התמונות או שהמכסה נגמרה. נסה שוב מאוחר יותר.") from exc
            if code in (401, 403):
                raise ImageGenError("מפתח Gemini לא מורשה ליצור תמונות. ייתכן שצריך להפעיל חיוב בפרויקט.") from exc
            if code == 404:
                raise ImageGenError(f"מודל התמונות {self.model} לא זמין בחשבון שלך.") from exc
            raise ImageGenError(f"יצירת התמונה נכשלה: {exc}") from exc
        for candidate in getattr(response, "candidates", None) or []:
            for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    mime = getattr(inline, "mime_type", "") or "image/png"
                    return inline.data, ".jpg" if "jpeg" in mime else ".png"
        raise ImageGenError("המודל לא החזיר תמונה (ייתכן שהבקשה נחסמה על ידי מסנן התוכן).")


def generate_images(
    maker: Any,
    prompts: List[str],
    save: Callable[[bytes, str, str], Any],
    log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> int:
    """Make one image per prompt; one failure does not stop the rest unless all fail."""
    log = log or (lambda _m: None)
    made, last_error = 0, None
    for index, prompt in enumerate(prompts, start=1):
        if progress:
            progress(index - 1, len(prompts))
        try:
            data, suffix = maker.make(prompt)
        except ImageGenError as exc:
            last_error = exc
            log(f"Image {index}/{len(prompts)} failed: {exc}")
            if "מכסה" in str(exc) or "מורשה" in str(exc) or "לא זמין" in str(exc):
                break  # the same error will repeat for every image
            continue
        save(data, suffix, prompt)
        made += 1
        log(f"Image {index}/{len(prompts)} ready")
    if made == 0 and last_error:
        raise last_error
    return made
