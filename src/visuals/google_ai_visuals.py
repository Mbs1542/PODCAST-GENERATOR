"""
Google AI Visual Generation Module
===================================

Integrates Google's Imagen 4 for image generation and VEO for video generation
to create high-quality educational visuals including network maps, architecture
diagrams, slides, and animated scenes.

Models supported:
    - Imagen 4: imagen-4.0-generate-001
    - Imagen 3: imagen-3.0-generate-001, imagen-3.0-fast-generate-001
    - VEO: veo-2.0-generate-001 (video generation)

Author: M.B.S Studio
Version: 1.0.0
"""

from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from PIL import Image

from ..utils import RunPaths, Settings, get_logger


# Visual generation modes
VisualMode = Literal["network_map", "architecture", "slide_background", "concept_illustration", "animation"]

# Topic color schemes for tests and theming
TOPIC_COLOR_SCHEMES = {
    "azure": "professional blue",
    "default": "neutral modern",
}

# Default prompts for different visual types
DEFAULT_PROMPTS = {
    "network_map": (
        "Professional educational network diagram highlighting {concept_count} connected ideas about {topic}. "
        "Clean modern design with labeled nodes, balanced layout, high contrast blue and gray palette, "
        "suitable for executive presentations, 4K quality."
    ),
    "architecture": (
        "Modern cloud workflow overview for {topic}. "
        "Show {concept_count} modular components with directional arrows, subtle gradients, "
        "enterprise-ready look, easy to follow, crisp vector style."
    ),
    "slide_background": (
        "Abstract professional background for a presentation about {topic}. "
        "Smooth gradients, geometric accents, {color_scheme} palette, minimal clutter, "
        "designed for overlaying Hebrew text cleanly, 16:9 aspect ratio."
    ),
    "concept_illustration": (
        "Educational illustration depicting {concept_desc}. "
        "Iconography, modern infographic style, clearly separated sections, "
        "accessible color palette, designed for training materials."
    ),
    "animation": (
        "Create a short educational animation illustrating {concept_desc}. "
        "Smooth camera moves, layered gradients, elegant motion graphics, "
        "professional explainer video tone."
    ),
}

@dataclass
class GoogleAIVisualGenerator:
    """
    Generate high-quality educational visuals using Google's AI models.
    
    Supports Imagen 4 for static images and VEO for video generation.
    Automatically selects appropriate models and prompts based on content.
    """
    
    settings: Settings
    
    def __post_init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._imagen_client = None
        self._veo_client = None
        self._placeholder_events: List[Dict[str, str]] = []
        self.visual_text_language = str(getattr(self.settings, "visual_text_language", "en") or "en").lower()
        # Trace current visual config for diagnostics
        self.logger.info(
            "Visual generator init | mode=%s | enable_visuals=%s | imagen_model=%s | veo_model=%s | gemini_key=%s",
            getattr(self.settings, "visual_generator", "manim"),
            getattr(self.settings, "enable_visuals", True),
            getattr(self.settings, "imagen_model", "unset"),
            getattr(self.settings, "veo_model", "unset"),
            "yes" if getattr(self.settings, "gemini_api_key", "") else "no",
        )
        self._initialize_clients()
    
    def _initialize_clients(self) -> None:
        """Initialize Google AI clients using the Gemini API."""
        if not self.settings.gemini_api_key:
            self.logger.warning("GEMINI_API_KEY not configured. Google AI visuals will be disabled.")
            return
        
        # Configure google.generativeai if available (needed for tests and legacy clients)
        try:
            import google.generativeai as genai_config  # type: ignore
            genai_config.configure(api_key=self.settings.gemini_api_key)
        except ImportError:
            pass

        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
            
            self._genai_client = genai.Client(api_key=self.settings.gemini_api_key)
            self._genai_types = types
            
            # Enforce Imagen 4 only
            self._imagen_model = "imagen-4.0-generate-001"
            self._imagen_client = self._genai_client
            self.logger.info("Initialized Google AI Client with Imagen model: %s", self._imagen_model)
            
            # VEO initialization
            veo_model = getattr(self.settings, 'veo_model', 'veo-2.0-generate-001')
            self._veo_model = veo_model
            self._veo_client = self._genai_client
            self.logger.info("Initialized Google AI Client with VEO model: %s", veo_model)
            
        except ImportError:
            self.logger.error("google-genai package not installed. Run: pip install google-genai")
            raise
        except Exception as exc:
            self.logger.error("Failed to initialize Google AI clients: %s", exc)
            raise
    
    @property
    def is_available(self) -> bool:
        """Check if Google AI services are available."""
        if not self._imagen_client:
            return False
        if getattr(self, "_genai_client", None):
            return True
        return (
            hasattr(self._imagen_client, "ImageGenerationModel")
            or hasattr(self._imagen_client, "generate_images")
        )

    def status_summary(self) -> str:
        """Human-friendly status string for UI/logging."""
        if not getattr(self.settings, "gemini_api_key", ""):
            return "GEMINI_API_KEY missing – Google AI visuals כבויים"
        if getattr(self, "_genai_client", None):
            return f"Ready (google.genai, model={getattr(self, '_imagen_model', 'unknown')})"
        if self._imagen_client:
            if hasattr(self._imagen_client, "ImageGenerationModel"):
                return f"Legacy client loaded (model={getattr(self, '_imagen_model', 'unknown')})"
            if hasattr(self._imagen_client, "generate_images"):
                return f"Legacy client (generate_images API) model={getattr(self, '_imagen_model', 'unknown')}"
            return "Legacy client loaded but no Imagen image API; install google-genai or upgrade google-generativeai>=0.6.0"
        return "Google AI client not initialized"

    def _sanitize_prompt_text(self, text: Optional[str]) -> str:
        """Return prompt text respecting visual_text_language policy."""
        if not text:
            return ""
        if self.visual_text_language.startswith("he"):
            return " ".join(text.split())
        cleaned = text.encode("ascii", "ignore").decode("ascii", errors="ignore")
        return " ".join(cleaned.split())

    def _apply_language_policy(self, text: str) -> str:
        """Normalize text according to configured visual language preference."""
        return self._sanitize_prompt_text(text)

    def _describe_topic(self, metadata: Dict) -> str:
        topic = metadata.get("topic") or metadata.get("summary") or "learning content"
        safe_topic = self._sanitize_prompt_text(topic)
        return safe_topic or "learning content"

    def _describe_concept(self, concept: str) -> str:
        safe = self._sanitize_prompt_text(concept)
        return safe or "a key learning concept"
    
    def generate_network_map(
        self,
        concepts: List[str],
        metadata: Dict,
        output_path: Path,
        style: str = "modern",
    ) -> Optional[Path]:
        """
        Generate a network/architecture map image using Imagen 4.
        
        Args:
            concepts: List of concepts to visualize in the map
            metadata: Metadata dict containing topic and context
            output_path: Path to save the generated image
            style: Visual style (modern, technical, simple)
            
        Returns:
            Path to generated image, or None if generation failed
        """
        if not self.is_available:
            self.logger.warning("Google AI not available for network map generation; will use placeholder")
        
        topic_desc = self._describe_topic(metadata)
        concept_count = max(len(concepts), 3)
        prompt = DEFAULT_PROMPTS["network_map"].format(
            topic=topic_desc,
            concept_count=concept_count,
        )
        
        return self._generate_image(prompt, output_path, aspect_ratio="16:9")
    
    def generate_architecture_diagram(
        self,
        components: List[str],
        connections: List[Tuple[str, str]],
        metadata: Dict,
        output_path: Path,
    ) -> Optional[Path]:
        """
        Generate an architecture diagram using Imagen 4.
        
        Args:
            components: List of architecture components
            connections: List of (source, target) connection tuples
            metadata: Metadata dict containing topic and context
            output_path: Path to save the generated image
            
        Returns:
            Path to generated image, or None if generation failed
        """
        if not self.is_available:
            return None
        
        topic_desc = self._describe_topic(metadata)
        concept_count = max(len(components), len(connections), 4)
        
        prompt = DEFAULT_PROMPTS["architecture"].format(
            topic=topic_desc,
            concept_count=concept_count,
        )
        
        return self._generate_image(prompt, output_path, aspect_ratio="16:9")
    
    def generate_slide_background(
        self,
        topic: str,
        color_scheme: str = "professional blue",
        output_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        Generate a professional slide background image.
        
        Args:
            topic: Slide topic for context
            color_scheme: Color scheme description
            output_path: Path to save the generated image
            
        Returns:
            Path to generated image, or None if generation failed
        """
        if not self.is_available:
            return None
        
        topic_desc = self._sanitize_prompt_text(topic) or "learning content"
        prompt = DEFAULT_PROMPTS["slide_background"].format(
            topic=topic_desc,
            color_scheme=color_scheme
        )
        
        return self._generate_image(prompt, output_path, aspect_ratio="16:9")
    
    def generate_concept_illustration(
        self,
        concept: str,
        context: str,
        output_path: Path,
    ) -> Optional[Path]:
        """
        Generate an educational illustration for a concept.
        
        Args:
            concept: The concept to illustrate
            context: Additional context for the illustration
            output_path: Path to save the generated image
            
        Returns:
            Path to generated image, or None if generation failed
        """
        if not self.is_available:
            return None
        
        concept_desc = self._describe_concept(concept)
        prompt = DEFAULT_PROMPTS["concept_illustration"].format(
            concept_desc=concept_desc
        )
        
        return self._generate_image(prompt, output_path, aspect_ratio="1:1")
    
    def generate_animation_scene(
        self,
        concept: str,
        duration_seconds: int = 5,
        output_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        Generate a short animation scene using VEO.
        
        Args:
            concept: The concept to animate
            duration_seconds: Target duration in seconds (5-60)
            output_path: Path to save the generated video
            
        Returns:
            Path to generated video, or None if generation failed
        """
        if not self.is_available:
            self.logger.warning("Google AI not available for animation generation")
            return None
        
        if not self._veo_client:
            self.logger.warning("VEO client not initialized")
            return None
        
        concept_desc = self._describe_concept(concept)
        prompt = DEFAULT_PROMPTS["animation"].format(concept_desc=concept_desc)
        
        return self._generate_video(prompt, output_path, duration_seconds)
    
    def _generate_image(
        self,
        prompt: str,
        output_path: Optional[Path],
        aspect_ratio: str = "16:9",
        num_images: int = 1,
    ) -> Optional[Path]:
        """
        Generate images using Google AI Imagen 3 with fallback to placeholder.
        
        Tries to use the real Imagen 3 API first. If that fails (due to API
        limitations, quota, or unavailability), falls back to generating
        professional placeholder images.
        
        Args:
            prompt: Text prompt for image generation
            output_path: Path to save the generated image
            aspect_ratio: Image aspect ratio (16:9, 1:1, 4:3)
            num_images: Number of images to generate (1-4)
            
        Returns:
            Path to generated image, or None if generation failed
        """
        if not output_path:
            return None
        
        # Log the full prompt for verification
        self.logger.info("=" * 60)
        self.logger.info("[IMAGEN PROMPT] Output: %s", output_path.name)
        self.logger.info("[IMAGEN PROMPT] Full prompt:")
        self.logger.info("  %s", prompt)
        self.logger.info("[IMAGEN PROMPT] Aspect ratio: %s, Model: %s", 
                        aspect_ratio, getattr(self, '_imagen_model', 'unknown'))
        self.logger.info("=" * 60)
        
        # Parse aspect ratio to dimensions
        aspect_map = {
            "16:9": (1920, 1080),
            "1:1": (1080, 1080),
            "4:3": (1440, 1080),
            "9:16": (1080, 1920),
        }
        width, height = aspect_map.get(aspect_ratio, (1920, 1080))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Enforce Imagen 4 only per directive
        if "imagen-4.0" not in str(getattr(self, "_imagen_model", "")):
            raise RuntimeError(f"Imagen model must be imagen-4.0; got {getattr(self, '_imagen_model', 'unset')}")

        try:
            def _write_bytes(data: bytes) -> Path:
                with open(output_path, 'wb') as f:
                    f.write(data)
                if (not output_path.exists()) or output_path.stat().st_size == 0:
                    raise ValueError("File write failed")
                return output_path

            if getattr(self, "_genai_client", None):
                attempts = 4
                backoff_base = 1.5
                for attempt in range(1, attempts + 1):
                    try:
                        self.logger.info(
                            "Attempting Imagen generation with model: %s (attempt %d/%d)",
                            self._imagen_model,
                            attempt,
                            attempts,
                        )
                        result = self._genai_client.models.generate_images(
                            model=self._imagen_model,
                            prompt=prompt,
                            config=self._genai_types.GenerateImagesConfig(
                                number_of_images=num_images,
                                aspect_ratio=aspect_ratio,
                            ),
                        )
                        self.logger.info("DEBUG RESPONSE DIR: %s", dir(result))
                        # Debug response structure for Imagen 4
                        if result and hasattr(result, 'generated_images') and result.generated_images:
                            image_data = result.generated_images[0]
                            self.logger.info(
                                "DEBUG GENERATED_IMAGE STRUCTURE: %s",
                                dir(image_data),
                            )
                            # Try multiple extraction paths for Imagen 4
                            if hasattr(image_data, 'image') and hasattr(image_data.image, 'image_bytes'):
                                _write_bytes(image_data.image.image_bytes)
                                self.logger.info("✅ Imagen generated image: %s", output_path)
                                return output_path
                            if hasattr(image_data, 'image_bytes'):
                                _write_bytes(image_data.image_bytes)
                                self.logger.info("✅ Imagen generated image: %s", output_path)
                                return output_path
                            if hasattr(image_data, 'bytes'):
                                _write_bytes(image_data.bytes)
                                self.logger.info("✅ Imagen generated image: %s", output_path)
                                return output_path
                            if hasattr(image_data, 'image'):
                                pil_image = image_data.image
                                if hasattr(pil_image, '_pil_image'):
                                    pil_image._pil_image.save(str(output_path), quality=95)
                                else:
                                    pil_image.save(str(output_path), quality=95)
                                if (not output_path.exists()) or output_path.stat().st_size == 0:
                                    raise ValueError("File write failed")
                                self.logger.info("✅ Imagen generated image: %s", output_path)
                                return output_path
                            # Last-resort dict-like handling
                            if isinstance(image_data, dict):
                                possible = (
                                    image_data.get("image_bytes")
                                    or image_data.get("bytes")
                                    or image_data.get("image", {}).get("image_bytes")
                                )
                                if possible:
                                    _write_bytes(possible)
                                    self.logger.info("✅ Imagen generated image: %s", output_path)
                                    return output_path
                            raise ValueError(f"Unexpected Imagen response format: {dir(image_data)}")
                        else:
                            raise ValueError("Imagen API returned no images")
                    except Exception as exc:
                        # Log full traceback so upstream callers can diagnose (e.g., numpy/cv2 failures)
                        self.logger.exception(
                            "Imagen API attempt %d/%d failed",
                            attempt,
                            attempts,
                        )
                        if attempt < attempts:
                            backoff = min(8.0, backoff_base * attempt)
                            self.logger.info("Retrying after %.1fs due to network/latency issue", backoff)
                            time.sleep(backoff)
                self.logger.warning(
                    "Imagen API failed after %d attempts for model %s. Falling back to placeholder.",
                    attempts,
                    self._imagen_model,
                )
                self._generate_placeholder_image(prompt, output_path, aspect_ratio=aspect_ratio)
                return output_path if output_path.exists() else None
            else:
                self.logger.warning("Google AI client not initialized for Imagen 4 generation; creating placeholder.")
                self._generate_placeholder_image(prompt, output_path, aspect_ratio=aspect_ratio)
                return output_path if output_path.exists() else None
        except Exception as exc:
            # Surface full traceback instead of silent fail, but fall back to placeholder to keep pipeline alive
            self.logger.exception("Imagen generation raised exception; generating placeholder")
            try:
                self._generate_placeholder_image(prompt, output_path, aspect_ratio=aspect_ratio)
                return output_path if output_path.exists() else None
            except Exception:
                raise

    def _try_generate_image_legacy(
        self,
        prompt: str,
        output_path: Path,
        num_images: int,
        aspect_ratio: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempt Imagen generation using the legacy google.generativeai client.
        
        Returns:
            (success, placeholder_reason)
        """
        try:
            imagen_model_cls = getattr(self._imagen_client, "ImageGenerationModel", None)
            if imagen_model_cls is None:
                return False, "Legacy Imagen client missing ImageGenerationModel (install google-genai>=0.3.0)"
            model = imagen_model_cls(self._imagen_model)
            result = model.generate_images(
                prompt=prompt,
                number_of_images=num_images,
                aspect_ratio=aspect_ratio,
            )
            images = getattr(result, "images", None)
            if images:
                image = images[0]
                if hasattr(image, "save"):
                    image.save(str(output_path), quality=95)
                elif hasattr(image, "image_bytes"):
                    with open(output_path, "wb") as f:
                        f.write(image.image_bytes)
                else:
                    return False, "Imagen legacy response had no saveable image content"
                self.logger.info("✅ Imagen (legacy) generated image: %s", output_path)
                return True, None
            return False, "Imagen legacy API returned no images"
        except Exception as exc:
            self.logger.exception("Imagen legacy API failed")
            return False, f"{exc.__class__.__name__}: {exc}"

    def _try_generate_image_functional(
        self,
        prompt: str,
        output_path: Path,
        num_images: int,
        aspect_ratio: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempt Imagen generation using functional generate_images (google-generativeai >=0.6).
        
        Returns:
            (success, placeholder_reason)
        """
        try:
            generate_images_fn = getattr(self._imagen_client, "generate_images", None)
            if generate_images_fn is None:
                return False, "Legacy Imagen client missing generate_images function"
            result = generate_images_fn(
                model=self._imagen_model,
                prompt=prompt,
                number_of_images=num_images,
                aspect_ratio=aspect_ratio,
            )
            images = getattr(result, "images", None)
            if images:
                image = images[0]
                if hasattr(image, "save"):
                    image.save(str(output_path), quality=95)
                elif hasattr(image, "image_bytes"):
                    with open(output_path, "wb") as f:
                        f.write(image.image_bytes)
                else:
                    return False, "Imagen functional response had no saveable image content"
                self.logger.info("✅ Imagen (functional) generated image: %s", output_path)
                return True, None
            return False, "Imagen functional API returned no images"
        except Exception as exc:
            self.logger.exception("Imagen functional API failed")
            return False, f"{exc.__class__.__name__}: {exc}"
    
    def _generate_video(
        self,
        prompt: str,
        output_path: Optional[Path],
        duration_seconds: int = 5,
    ) -> Optional[Path]:
        """
        Generate videos using VEO API or create animated placeholder videos.
        
        This method tries to use the VEO API for video generation. If VEO is not
        available or fails, it generates a professional animated placeholder video
        using moviepy with animated text and gradients.
        
        Args:
            prompt: Text prompt for video generation
            output_path: Path to save the generated video
            duration_seconds: Target duration in seconds
            
        Returns:
            Path to generated video, or None if generation failed
        """
        if not output_path:
            return None
            
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.logger.info("Generating video for: %s...", prompt[:100])
        
        # Try VEO API first if available (using new google.genai Client)
        if self._veo_client and hasattr(self, '_genai_client') and self._genai_client:
            try:
                self.logger.info("Attempting VEO video generation with model: %s", self._veo_model)
                
                # Try to generate video using the new Client API
                try:
                    result = self._genai_client.models.generate_videos(
                        model=self._veo_model,
                        prompt=prompt,
                        config=self._genai_types.GenerateVideosConfig(
                            duration_seconds=duration_seconds,
                            aspect_ratio="16:9",
                        )
                    )
                    
                    # VEO operations are async - poll for completion
                    max_wait = 300  # 5 minutes max
                    wait_time = 0
                    while not result.done and wait_time < max_wait:
                        time.sleep(10)
                        wait_time += 10
                        self.logger.debug("Waiting for VEO... %ds", wait_time)
                        result = self._genai_client.operations.get(result.name)
                    
                    if result.done and result.result:
                        if hasattr(result.result, 'generated_videos') and result.result.generated_videos:
                            video_data = result.result.generated_videos[0]
                            if hasattr(video_data, 'video') and hasattr(video_data.video, 'video_bytes'):
                                with open(output_path, 'wb') as f:
                                    f.write(video_data.video.video_bytes)
                                self.logger.info("✅ VEO generated video: %s", output_path)
                                return output_path
                            
                except AttributeError as e:
                    self.logger.info("VEO API not available via Client: %s", e)
                    
            except Exception as exc:
                self.logger.warning("VEO API failed: %s - using placeholder", exc)
        
        # Fallback: Generate animated placeholder video using moviepy
        return self._generate_placeholder_video(prompt, output_path, duration_seconds)
    
    def _generate_placeholder_video(
        self,
        prompt: str,
        output_path: Path,
        duration_seconds: int = 5,
    ) -> Optional[Path]:
        """
        Generate an animated placeholder video using moviepy.
        
        Creates a professional-looking video with animated text and gradients
        when VEO is not available.
        """
        try:
            # MoviePy 2.0 compatible imports
            from moviepy import (
                ColorClip, TextClip, CompositeVideoClip, 
                concatenate_videoclips, VideoClip
            )
            from moviepy.video.fx import CrossFadeIn, CrossFadeOut
            from PIL import Image, ImageDraw
            import numpy as np
            
            width, height = 1920, 1080
            fps = 24
            
            # Create gradient frames for animation
            def make_gradient_frame(t):
                """Create an animated gradient background."""
                img = Image.new('RGB', (width, height))
                draw = ImageDraw.Draw(img)
                
                # Animated gradient colors
                phase = (t / duration_seconds) * 2 * 3.14159
                color_shift = int(50 * (1 + np.sin(phase)))
                
                for y in range(height):
                    ratio = y / height
                    r = int(10 + (30 * ratio) + color_shift * 0.3)
                    g = int(15 + (70 * ratio))
                    b = int(50 + (180 * ratio) - color_shift * 0.2)
                    # Clamp values
                    r = max(0, min(255, r))
                    g = max(0, min(255, g))
                    b = max(0, min(255, b))
                    draw.line([(0, y), (width, y)], fill=(r, g, b))
                
                return np.array(img)
            
            # Create background clip - MoviePy 2.0 API
            background = VideoClip(make_gradient_frame, duration=duration_seconds)
            background = background.with_fps(fps)
            
            # Create text overlay with the prompt (truncated)
            display_text = prompt[:80] + "..." if len(prompt) > 80 else prompt
            
            try:
                text_clip = TextClip(
                    text=display_text,
                    font_size=36,
                    color='white',
                    font='Arial',
                    size=(width - 200, None),
                    method='caption',
                )
                # MoviePy 2.0: use with_position and with_duration
                text_clip = text_clip.with_position('center').with_duration(duration_seconds)
                
                # Add fade in/out using effects
                text_clip = text_clip.with_effects([CrossFadeIn(0.5), CrossFadeOut(0.5)])
                
                # Composite
                final = CompositeVideoClip([background, text_clip])
            except Exception:
                # If text fails, just use background
                final = background
            
            # Write video
            final.write_videofile(
                str(output_path),
                fps=fps,
                codec='libx264',
                audio=False,
                preset='ultrafast',
                logger=None,
            )
            
            self.logger.info("Generated placeholder video: %s", output_path)
            return output_path
            
        except ImportError as e:
            self.logger.error("moviepy not available for video generation: %s", e)
            return None
        except Exception as exc:
            self.logger.error("Placeholder video generation failed: %s", exc)
            return None
    
    def _generate_placeholder_image(
        self,
        prompt: str,
        output_path: Path,
        aspect_ratio: str = "16:9",
    ) -> None:
        """
        Generate a placeholder image when the AI service is unavailable.
        
        Creates a professional-looking gradient background with text overlay.
        """
        # Parse aspect ratio
        if aspect_ratio == "16:9":
            width, height = 1920, 1080
        elif aspect_ratio == "1:1":
            width, height = 1080, 1080
        elif aspect_ratio == "4:3":
            width, height = 1440, 1080
        else:
            width, height = 1920, 1080
        
        # Create gradient background
        from PIL import Image, ImageDraw, ImageFont
        
        img = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(img)
        
        # Create gradient
        for y in range(height):
            ratio = y / height
            r = int(5 + (30 * ratio))
            g = int(8 + (64 * ratio))
            b = int(38 + (175 * ratio))
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        
        # Add subtle badge + caption (no raw prompt text)
        try:
            badge_font = ImageFont.truetype("arial.ttf", 42)
            caption_font = ImageFont.truetype("arial.ttf", 24)
        except OSError:
            badge_font = ImageFont.load_default()
            caption_font = badge_font
        
        badge_text = "תמונה זמנית"
        badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_width = badge_bbox[2] - badge_bbox[0]
        badge_height = badge_bbox[3] - badge_bbox[1]
        badge_x = (width - badge_width) // 2
        badge_y = height // 3
        draw.text((badge_x + 2, badge_y + 2), badge_text, fill=(0, 0, 0, 120), font=badge_font)
        draw.text((badge_x, badge_y), badge_text, fill=(255, 255, 255), font=badge_font)
        
        summary_text = "התמונה תיווצר מחדש כאשר שירות ה-AI יחזור לפעול."
        summary_bbox = draw.textbbox((0, 0), summary_text, font=caption_font)
        summary_width = summary_bbox[2] - summary_bbox[0]
        summary_x = (width - summary_width) // 2
        summary_y = badge_y + badge_height + 40
        draw.text((summary_x + 1, summary_y + 1), summary_text, fill=(0, 0, 0, 150), font=caption_font)
        draw.text((summary_x, summary_y), summary_text, fill=(220, 220, 220), font=caption_font)
        
        # Save image
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), quality=95)
        self.logger.info("Placeholder image created: %s", output_path)
    
    def _generate_image_with_fallback(self, prompt: str, output_path: Path, aspect_ratio: str = "16:9") -> Optional[Path]:
        """
        Attempt Imagen generation and fall back to placeholder on any failure.
        """
        try:
            return self._generate_image(prompt, output_path, aspect_ratio=aspect_ratio)
        except Exception as exc:
            self.logger.warning("Imagen generation failed, using placeholder: %s", exc, exc_info=True)
            try:
                self._generate_placeholder_image(prompt, output_path, aspect_ratio=aspect_ratio)
                return output_path
            except Exception as placeholder_exc:  # pragma: no cover - defensive
                self.logger.error("Placeholder generation failed after Imagen error: %s", placeholder_exc, exc_info=True)
                return None
    
    def _generate_educational_placeholder(
        self,
        prompt: str,
        output_path: Path,
        dimensions: Tuple[int, int] = (1920, 1080),
    ) -> None:
        """
        Generate a professional educational visual placeholder.
        
        Creates a visually appealing slide with gradient background,
        icon, and prompt-based text content.
        """
        from PIL import Image, ImageDraw, ImageFont
        
        width, height = dimensions
        img = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(img)
        
        # Extract keywords from prompt for theming
        prompt_lower = prompt.lower()
        
        # Choose color scheme based on content
        if any(word in prompt_lower for word in ['network', 'architecture', 'diagram', 'cloud', 'azure']):
            colors = {"bg_start": (10, 30, 60), "bg_end": (30, 80, 140), "accent": (0, 162, 232)}
        elif any(word in prompt_lower for word in ['music', 'song', 'שיר', 'מוזיקה']):
            colors = {"bg_start": (40, 20, 50), "bg_end": (100, 50, 120), "accent": (200, 100, 220)}
        elif any(word in prompt_lower for word in ['education', 'school', 'בית ספר', 'חינוך']):
            colors = {"bg_start": (20, 50, 40), "bg_end": (50, 120, 90), "accent": (100, 200, 150)}
        else:
            colors = {"bg_start": (15, 25, 45), "bg_end": (35, 65, 115), "accent": (100, 180, 255)}
        
        # Create gradient background
        for y in range(height):
            ratio = y / height
            r = int(colors["bg_start"][0] + (colors["bg_end"][0] - colors["bg_start"][0]) * ratio)
            g = int(colors["bg_start"][1] + (colors["bg_end"][1] - colors["bg_start"][1]) * ratio)
            b = int(colors["bg_start"][2] + (colors["bg_end"][2] - colors["bg_start"][2]) * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        
        # Add decorative elements
        accent = colors["accent"]
        # Top accent line
        draw.rectangle([(0, 0), (width, 8)], fill=accent)
        # Bottom accent line
        draw.rectangle([(0, height - 8), (width, height)], fill=accent)
        # Corner decorations
        draw.ellipse([(width - 200, -100), (width + 100, 200)], fill=(accent[0], accent[1], accent[2], 30), outline=None)
        draw.ellipse([(-100, height - 200), (200, height + 100)], fill=(accent[0], accent[1], accent[2], 30), outline=None)
        
        # Load fonts
        try:
            title_font = ImageFont.truetype("arial.ttf", 72)
            caption_font = ImageFont.truetype("arial.ttf", 26)
        except OSError:
            title_font = ImageFont.load_default()
            caption_font = title_font
        
        tagline = "המערכת לא הצליחה ליצור את הויז'ואל המבוקש."
        support_line = "בדוק/י את חיבור Gemini/Imagen ונסה/י להריץ מחדש."
        filename_note = f"קובץ יעד: {output_path.stem}" if output_path else "קובץ יעד לא זמין"
        
        # Draw icon/symbol
        icon_y = height // 3 - 60
        icon_size = 100
        icon_x = width // 2 - icon_size // 2
        draw.ellipse(
            [(icon_x, icon_y), (icon_x + icon_size, icon_y + icon_size)],
            fill=accent,
            outline=(255, 255, 255),
            width=3
        )
        # Draw a simple play/learn icon inside
        draw.polygon([
            (icon_x + 35, icon_y + 25),
            (icon_x + 35, icon_y + 75),
            (icon_x + 75, icon_y + 50)
        ], fill=(255, 255, 255))
        
        # Draw badge/title
        badge_text = "תמונה ממוחשבת זמנית"
        badge_bbox = draw.textbbox((0, 0), badge_text, font=title_font)
        badge_width = badge_bbox[2] - badge_bbox[0]
        badge_x = (width - badge_width) // 2
        badge_y = icon_y - 140
        draw.text((badge_x + 3, badge_y + 3), badge_text, fill=(0, 0, 0, 160), font=title_font)
        draw.text((badge_x, badge_y), badge_text, fill=(255, 255, 255), font=title_font)
        
        # Draw tagline under icon
        tagline_bbox = draw.textbbox((0, 0), tagline, font=caption_font)
        tagline_width = tagline_bbox[2] - tagline_bbox[0]
        tagline_x = (width - tagline_width) // 2
        tagline_y = icon_y + icon_size + 50
        draw.text((tagline_x + 1, tagline_y + 1), tagline, fill=(0, 0, 0, 150), font=caption_font)
        draw.text((tagline_x, tagline_y), tagline, fill=(255, 255, 255), font=caption_font)

        support_bbox = draw.textbbox((0, 0), support_line, font=caption_font)
        support_width = support_bbox[2] - support_bbox[0]
        support_x = (width - support_width) // 2
        support_y = tagline_y + 60
        draw.text((support_x + 1, support_y + 1), support_line, fill=(0, 0, 0, 150), font=caption_font)
        draw.text((support_x, support_y), support_line, fill=(230, 230, 230), font=caption_font)

        file_bbox = draw.textbbox((0, 0), filename_note, font=caption_font)
        file_width = file_bbox[2] - file_bbox[0]
        file_x = (width - file_width) // 2
        file_y = support_y + 48
        draw.text((file_x + 1, file_y + 1), filename_note, fill=(0, 0, 0, 150), font=caption_font)
        draw.text((file_x, file_y), filename_note, fill=(200, 200, 200), font=caption_font)
        
        # Add footer caption
        footer_text = "M.B.S Studio • הויז'ואל יווצר מחדש אוטומטית לאחר התחברות"
        bbox = draw.textbbox((0, 0), footer_text, font=caption_font)
        footer_width = bbox[2] - bbox[0]
        draw.text(
            ((width - footer_width) // 2, height - 50),
            footer_text,
            fill=(180, 180, 180),
            font=caption_font
        )
        
        # Save image
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), quality=95)
        self.logger.info("Educational placeholder created: %s", output_path)
    
    def _generate_from_visual_metadata(
        self,
        visual_metadata: Dict,
        run_paths: RunPaths,
        generate_images: bool = True,
        generate_videos: bool = False,
        max_images: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> List[Path]:
        """
        Generate visuals using detailed visual metadata with professional prompts.
        
        Uses the pre-generated prompts, styles, moods, and color palettes from
        visual_metadata.json for high-quality, contextually relevant images.
        
        Args:
            visual_metadata: Dict with images, video, and notes from visual_metadata.json
            run_paths: RunPaths for output locations
            generate_images: Whether to generate static images
            generate_videos: Whether to generate video clips
            
        Returns:
            List of paths to generated visual files
        """
        generated_paths: List[Path] = []
        
        # Log visual metadata info
        background = visual_metadata.get("background", "")
        images = visual_metadata.get("images", [])
        video_meta = visual_metadata.get("video", {})
        notes = visual_metadata.get("general_notes", {})
        
        self.logger.info("[VISUAL METADATA] Background: %s", background[:100] + "..." if len(background) > 100 else background)
        self.logger.info("[VISUAL METADATA] Image prompts: %d", len(images))
        self.logger.info("[VISUAL METADATA] Video metadata: %s", "Yes" if video_meta.get("prompt") else "No")
        self.logger.info("[VISUAL METADATA] Aesthetics: %s", notes.get("aesthetics", "Not specified")[:100])
        
        run_paths.log(f"[Visual Metadata] Background: {background[:80]}...")
        run_paths.log(
            f"[Visual Metadata] {len(images)} image prompts available"
            + (f" (capped at {max_images})" if max_images else "")
        )
        
        topic_text = (metadata or {}).get("topic", "") if metadata else ""
        topic_lower = topic_text.lower()
        non_cloud_topic = not any(word in topic_lower for word in ["azure", "cloud"])
        
        if generate_images and images:
            self.logger.info("=" * 60)
            self.logger.info("      GENERATING IMAGES FROM VISUAL METADATA")
            self.logger.info("=" * 60)
            
            for idx, img_data in enumerate(images):
                if max_images is not None and len(generated_paths) >= max_images:
                    self.logger.info(
                        "[VISUAL METADATA] Reached image cap (%d). Skipping remaining prompts.",
                        max_images,
                    )
                    run_paths.log(f"[Visual Metadata] Image cap reached ({max_images}); remaining prompts skipped.")
                    break
                img_index = img_data.get("index", idx + 1)
                title = img_data.get("title", f"Image {img_index}")
                prompt = img_data.get("prompt", "")
                style = img_data.get("style", "")
                mood = img_data.get("mood", "")
                color_palette = img_data.get("color_palette", "")
                context = img_data.get("background_context", "")
                
                if not prompt:
                    self.logger.warning("[VISUAL METADATA] Image %d has no prompt, skipping", img_index)
                    continue
                
                # Build enhanced prompt with style and mood
                enhanced_prompt = prompt
                if style:
                    # Append style hints to prompt if not already included
                    if style.lower() not in prompt.lower():
                        enhanced_prompt = f"{prompt} {style}"
                enhanced_prompt = self._apply_language_policy(enhanced_prompt)

                # Guard against Azure hallucinations for non-cloud topics
                if non_cloud_topic and "azure" in enhanced_prompt.lower():
                    warning_msg = (
                        f"Detected hallucinated 'Azure' context for topic '{topic_text}'. "
                        "Replacing prompt with abstract safe fallback."
                    )
                    self.logger.warning(warning_msg)
                    run_paths.log(f"⚠ {warning_msg}")
                    enhanced_prompt = (
                        f"Artistic abstract background representing {topic_text or 'the topic'}, "
                        "high quality, 4K, cinematic, no vendor branding."
                    )
                
                # Log detailed prompt info
                self.logger.info("-" * 60)
                self.logger.info("[IMAGE %d] Title: %s", img_index, title)
                self.logger.info("[IMAGE %d] Prompt: %s", img_index, enhanced_prompt[:200])
                self.logger.info("[IMAGE %d] Style: %s", img_index, style)
                self.logger.info("[IMAGE %d] Mood: %s", img_index, mood)
                self.logger.info("[IMAGE %d] Colors: %s", img_index, color_palette)
                self.logger.info("[IMAGE %d] Context: %s", img_index, context[:100] if context else "N/A")
                
                run_paths.log(f"[Image {img_index}] {title}")
                
                # Generate image with detailed prompt
                output_path = run_paths.visuals_dir / f"image_{img_index:02d}_{self._safe_filename(title)}.png"
                run_paths.log(
                    f"🎨 Generating Image {len(generated_paths) + 1}/{max_images or len(images)} – {title}"
                )
                result = self._generate_image_with_fallback(enhanced_prompt, output_path, aspect_ratio="16:9")
                
                if result:
                    generated_paths.append(result)
                    self.logger.info("[IMAGE %d] ✅ Generated: %s", img_index, result.name)
                else:
                    self.logger.warning("[IMAGE %d] ❌ Failed to generate", img_index)
                    run_paths.log(f"[Image {img_index}] Failed to generate (check logs for traceback)")
            
            self.logger.info("=" * 60)
            capped_total = max_images if max_images is not None else len(images)
            self.logger.info("      IMAGES COMPLETE: %d/%d generated", len(generated_paths), capped_total)
            self.logger.info("=" * 60)
        
        if generate_videos and video_meta.get("prompt"):
            self.logger.info("=" * 60)
            self.logger.info("      GENERATING VIDEO FROM VISUAL METADATA")
            self.logger.info("=" * 60)
            
            video_title = video_meta.get("title", "video")
            video_prompt = video_meta.get("prompt", "")
            video_style = video_meta.get("style", "")
            video_duration = video_meta.get("duration", 30)
            video_mood = video_meta.get("mood", "")
            video_scenes = video_meta.get("scene_structure", [])
            
            self.logger.info("[VIDEO] Title: %s", video_title)
            self.logger.info("[VIDEO] Duration: %ds", video_duration)
            self.logger.info("[VIDEO] Prompt: %s", video_prompt[:200])
            self.logger.info("[VIDEO] Style: %s", video_style)
            self.logger.info("[VIDEO] Mood: %s", video_mood)
            self.logger.info("[VIDEO] Scenes: %d", len(video_scenes))
            
            # Enhance prompt with style if not included
            enhanced_video_prompt = video_prompt
            if video_style and video_style.lower() not in video_prompt.lower():
                enhanced_video_prompt = f"{video_prompt} {video_style}"
            enhanced_video_prompt = self._apply_language_policy(enhanced_video_prompt)
            
            # Limit duration for VEO
            actual_duration = min(video_duration, 60)  # VEO max is usually around 60s
            
            output_path = run_paths.visuals_dir / f"video_{self._safe_filename(video_title)}.mp4"
            result = self._generate_video(enhanced_video_prompt, output_path, actual_duration)
            
            if result:
                generated_paths.append(result)
                self.logger.info("[VIDEO] ✅ Generated: %s", result.name)
                run_paths.log(f"[Video] Generated: {result.name}")
            else:
                self.logger.warning("[VIDEO] ❌ Failed to generate")
                run_paths.log("[Video] Generation failed")
        
        # Log final summary
        self.logger.info("=" * 70)
        self.logger.info("      VISUAL METADATA GENERATION - COMPLETE")
        self.logger.info("=" * 70)
        self.logger.info("[VISUAL METADATA] Total files generated: %d", len(generated_paths))
        for path in generated_paths:
            self.logger.info("[VISUAL METADATA]   - %s", path.name)
        self.logger.info("=" * 70)
        
        run_paths.log(f"[Visual Metadata] Complete: {len(generated_paths)} files generated")
        
        return generated_paths

    def _safe_filename(self, text: str, max_length: int = 30) -> str:
        """Convert text to a safe filename component."""
        if not text:
            return "untitled"
        # Remove non-ASCII and special characters
        safe = "".join(c if c.isalnum() or c in "- _" else "_" for c in text)
        safe = safe.strip("_- ").replace(" ", "_")
        return safe[:max_length] if safe else "untitled"

    def _extract_concepts_from_dialogue(self, dialogue_json: Dict, topic: str) -> List[str]:
        """
        Extract concepts from dialogue content when metadata lacks key_concepts.
        
        Analyzes dialogue turns to identify potential visual concepts based on
        keywords and context.
        
        Args:
            dialogue_json: Dialogue JSON with turns
            topic: Topic string for context
            
        Returns:
            List of extracted concept strings
        """
        concepts = []
        seen = set()
        
        # Start with topic-based concepts
        if topic:
            topic_words = [w.strip() for w in topic.split() if len(w.strip()) > 3]
            for word in topic_words[:3]:
                if word.lower() not in seen:
                    concepts.append(word)
                    seen.add(word.lower())
        
        # Extract from dialogue turns
        turns = dialogue_json.get("dialogue", [])
        for turn in turns:
            text = turn.get("text", "")
            # Look for educational keywords that could be visualized
            keywords = [
                w.strip(".,!?:;\"'()[]{}") 
                for w in text.split() 
                if len(w.strip(".,!?:;\"'()[]{}")) > 4
            ]
            for kw in keywords:
                if kw.lower() not in seen and len(concepts) < 10:
                    # Skip common Hebrew/English words
                    skip_words = {
                        'את', 'של', 'על', 'עם', 'זה', 'היא', 'הוא', 'אני', 'אתה',
                        'that', 'this', 'with', 'from', 'have', 'they', 'what', 'about',
                        'there', 'their', 'would', 'could', 'should', 'which', 'these'
                    }
                    if kw.lower() not in skip_words:
                        concepts.append(kw)
                        seen.add(kw.lower())
        
        return concepts[:8]  # Limit to 8 concepts
    
    def build_visuals_for_dialogue(
        self,
        dialogue_json: Dict,
        metadata: Dict,
        run_paths: RunPaths,
        generate_images: bool = True,
        generate_videos: bool = False,
        image_count: Optional[int] = None,
        visual_metadata: Optional[Dict] = None,
    ) -> List[Path]:
        """
        Generate visuals for a dialogue based on detected concepts or visual metadata.
        
        If visual_metadata is provided (from visual_metadata.json), uses the detailed
        prompts and specifications from that file. Otherwise, analyzes the dialogue
        and metadata to identify concepts that would benefit from visual representation.
        
        Args:
            dialogue_json: Dialogue JSON with turns
            metadata: Metadata dict with topic, concepts, etc.
            run_paths: RunPaths for output locations
            generate_images: Whether to generate static images (Imagen)
            generate_videos: Whether to generate video clips (VEO)
            image_count: Number of images to generate (overrides default logic)
            visual_metadata: Optional detailed visual metadata with prompts, styles, etc.
            
        Returns:
            List of paths to generated visual files
        """
        if not self.is_available:
            self.logger.warning("Google AI not available for visual generation")
            return []
        
        # Log comprehensive visual generation summary
        self.logger.info("=" * 70)
        self.logger.info("      GOOGLE AI VISUAL GENERATION - STARTING")
        self.logger.info("=" * 70)
        
        generated_paths: List[Path] = []
        
        # Determine target image count with explicit logging (always respect GUI config)
        if image_count is not None:
            target_count = int(image_count)
            self.logger.info("Using image_count from parameter: %d", target_count)
        else:
            target_count = getattr(self.settings, 'image_count', 5)
            self.logger.info(
                "Using image_count from settings: %d (type: %s)",
                target_count,
                type(target_count).__name__,
            )
        
        try:
            target_count = int(target_count)
        except (ValueError, TypeError):
            self.logger.warning("Invalid image_count value: %s, defaulting to 5", target_count)
            target_count = 5
        target_count = max(1, target_count)
        
        self.logger.info("Final target image count: %d", target_count)
        run_paths.log(f"[Google AI Visuals] Target image count: {target_count}")
        
        # Check if we have detailed visual metadata
        if visual_metadata and visual_metadata.get("images"):
            self.logger.info("[VISUAL GEN] Using detailed visual metadata with %d custom prompts!", len(visual_metadata.get("images", [])))
            run_paths.log(f"[Google AI] Using detailed visual metadata with {len(visual_metadata.get('images', []))} custom prompts")
            generated_paths = self._generate_from_visual_metadata(
                visual_metadata,
                run_paths,
                generate_images,
                generate_videos,
                max_images=target_count,
                metadata=metadata,
            )
            placeholder_events = self.consume_placeholder_events()
            if placeholder_events:
                run_paths.log("⚠ Google Imagen fell back to placeholder slides:")
                for event in placeholder_events[:10]:
                    run_paths.log(f"   • {event['name']}: {event['reason']}")
                if len(placeholder_events) > 10:
                    run_paths.log(f"   • ... ועוד {len(placeholder_events) - 10} פריימים")
                self.logger.warning(
                    "[VISUAL GEN] Placeholder slides generated for %d visual(s). Check GEMINI/Imagen status.",
                    len(placeholder_events),
                )
            return generated_paths
        else:
            self.logger.warning("[VISUAL GEN] No visual_metadata.json found! Generating generic AI images based on concepts.")
            self.logger.warning("[VISUAL GEN] For custom visuals, create a visual_metadata.json file with specific prompts, styles, and colors.")
            run_paths.log("[WARNING] No visual_metadata.json found - using generic AI generation. Create visual_metadata.json for custom prompts.")
        
        # Extract concepts from metadata
        key_concepts = metadata.get("key_concepts", [])
        topic = metadata.get("topic", "") or "Educational Content"
        summary = metadata.get("summary", "")
        # Precompute sanitized topic/summary descriptions for later use
        topic_desc = self._describe_topic(metadata)
        summary_desc = self._sanitize_prompt_text(summary) if summary else None
        
        # Log metadata being used
        self.logger.info("[VISUAL GEN] Topic: %s", topic)
        self.logger.info("[VISUAL GEN] Summary: %s", summary[:150] + "..." if len(summary) > 150 else summary)
        self.logger.info("[VISUAL GEN] Key Concepts from metadata: %d", len(key_concepts))
        for idx, concept in enumerate(key_concepts):
            self.logger.info("[VISUAL GEN]   %d. %s", idx + 1, concept)
        
        run_paths.log(f"[Google AI] Starting visual generation for topic: {topic}")
        run_paths.log(f"[Google AI] Metadata concepts: {key_concepts[:5] if key_concepts else 'None'}")
        
        # If no concepts in metadata, extract from dialogue
        if not key_concepts and dialogue_json:
            key_concepts = self._extract_concepts_from_dialogue(dialogue_json, topic)
            if key_concepts:
                self.logger.info("[VISUAL GEN] Extracted %d concepts from dialogue: %s", 
                               len(key_concepts), ", ".join(key_concepts[:5]))
                run_paths.log(f"[Google AI] Extracted concepts from dialogue: {key_concepts[:5]}")
        
        if generate_images:
            images_generated = 0
            
            # 1. Generate network map if we have multiple concepts (counts as 1 image)
            if len(key_concepts) >= 3 and images_generated < target_count:
                map_path = run_paths.visuals_dir / "network_map.png"
                run_paths.log(f"🎨 Generating Image {images_generated + 1}/{target_count} – concept map")
                try:
                    result = self.generate_network_map(key_concepts, metadata, map_path)
                except Exception as exc:
                    self.logger.warning("Network map generation failed, using placeholder: %s", exc, exc_info=True)
                    result = self._generate_placeholder_image("network map", map_path)
                if result:
                    generated_paths.append(result)
                    images_generated += 1
            
            # 2. Generate concept illustrations for key concepts
            concepts_to_illustrate = min(len(key_concepts), target_count - images_generated)
            for idx, concept in enumerate(key_concepts[:concepts_to_illustrate]):
                if images_generated >= target_count:
                    break
                concept_path = run_paths.visuals_dir / f"concept_{idx + 1}.png"
                run_paths.log(
                    f"🎨 Generating Image {images_generated + 1}/{target_count} – concept: {concept}"
                )
                try:
                    result = self.generate_concept_illustration(
                        concept=concept,
                        context=topic,
                        output_path=concept_path,
                    )
                except Exception as exc:
                    self.logger.warning("Concept illustration failed, using placeholder: %s", exc, exc_info=True)
                    result = self._generate_placeholder_image(concept, concept_path)
                if result:
                    generated_paths.append(result)
                    images_generated += 1
            
            # 3. Generate slide background (counts as 1 image)
            if images_generated < target_count:
                bg_path = run_paths.visuals_dir / "slide_background.png"
                run_paths.log(f"🎨 Generating Image {images_generated + 1}/{target_count} – slide background")
                try:
                    result = self.generate_slide_background(
                        topic=topic_desc,
                        output_path=bg_path,
                    )
                except Exception as exc:
                    self.logger.warning("Slide background generation failed, using placeholder: %s", exc, exc_info=True)
                    result = self._generate_placeholder_image(topic_desc, bg_path)
                if result:
                    generated_paths.append(result)
                    images_generated += 1
            
            # 4. If still need more images, generate topic-based visuals
            topic_visual_types = [
                ("overview", f"Educational overview infographic about {topic_desc}. Modern design, icons, key points highlighted."),
                ("summary", f"Visual summary diagram for {topic_desc}. Clean layout, main concepts connected."),
                ("intro", f"Title slide visual for presentation about {topic_desc}. Professional, engaging, modern style."),
                ("conclusion", f"Conclusion slide visual for {topic_desc}. Key takeaways, memorable design."),
            ]
            
            for visual_type, prompt in topic_visual_types:
                if images_generated >= target_count:
                    break
                visual_path = run_paths.visuals_dir / f"{visual_type}_visual.png"
                run_paths.log(
                    f"🎨 Generating Image {images_generated + 1}/{target_count} – {visual_type} visual"
                )
                result = self._generate_image_with_fallback(prompt, visual_path, aspect_ratio="16:9")
                if result:
                    generated_paths.append(result)
                    images_generated += 1
            
            # 5. If we have summary, generate summary-based visuals
            if summary_desc and images_generated < target_count:
                summary_path = run_paths.visuals_dir / "summary_infographic.png"
                summary_prompt = (
                    f"Educational infographic summarizing: {summary_desc[:200]}."
                    " Clear visual hierarchy, icons, modern style."
                )
                run_paths.log(
                    f"🎨 Generating Image {images_generated + 1}/{target_count} – summary infographic"
                )
                result = self._generate_image_with_fallback(summary_prompt, summary_path, aspect_ratio="16:9")
                if result:
                    generated_paths.append(result)
                    images_generated += 1
        
        if generate_videos:
            # Generate video clips using VEO or placeholder videos
            self.logger.info("Starting VEO video generation")
            videos_generated = 0
            max_videos = 3  # Limit video generation (expensive)
            
            # Video prompts based on content
            video_prompts = []
            
            # 1. Topic overview video
            video_prompts.append({
                "name": "topic_intro",
                "prompt": f"Smooth animated introduction for educational content about {topic_desc}. "
                         f"Modern motion graphics, professional style, subtle animations, "
                         f"gradient backgrounds transitioning, text appearing smoothly.",
                "duration": 5
            })
            
            # 2. Concept explanation videos
            for concept in key_concepts[:2]:  # Limit to 2 concept videos
                concept_desc = self._describe_concept(concept)
                safe_name = concept_desc[:20].replace(" ", "_")
                video_prompts.append({
                    "name": f"concept_{safe_name or 'concept'}",
                    "prompt": f"Educational animation explaining {concept_desc}. "
                             f"Visual metaphor, step-by-step reveal, professional style, "
                             f"clean modern design, subtle particle effects.",
                    "duration": 5
                })
            
            # 3. Conclusion video
            video_prompts.append({
                "name": "conclusion",
                "prompt": f"Professional conclusion animation for {topic_desc} presentation. "
                         f"Key points highlighted, modern motion graphics, satisfying ending, "
                         f"gradient fade, professional corporate style.",
                "duration": 4
            })
            
            for video_info in video_prompts:
                if videos_generated >= max_videos:
                    break
                    
                video_path = run_paths.visuals_dir / f"{video_info['name']}.mp4"
                result = self._generate_video(
                    prompt=video_info["prompt"],
                    output_path=video_path,
                    duration_seconds=video_info["duration"]
                )
                if result:
                    generated_paths.append(result)
                    videos_generated += 1
                    self.logger.info("Generated video %d/%d: %s", 
                                   videos_generated, max_videos, video_info["name"])
            
            self.logger.info("Generated %d video clips", videos_generated)
            run_paths.log(f"[Google AI] Generated {videos_generated} video clips")
        
        placeholder_events = self.consume_placeholder_events()
        if placeholder_events:
            run_paths.log("⚠ Google Imagen fell back to placeholder slides:")
            for event in placeholder_events[:10]:
                run_paths.log(f"   • {event['name']}: {event['reason']}")
            if len(placeholder_events) > 10:
                run_paths.log(f"   • ... ועוד {len(placeholder_events) - 10} פריימים")
            self.logger.warning(
                "[VISUAL GEN] Placeholder slides generated for %d visual(s). Check GEMINI/Imagen status.",
                len(placeholder_events),
            )
        
        # Log final summary
        self.logger.info("=" * 70)
        self.logger.info("      GOOGLE AI VISUAL GENERATION - COMPLETE")
        self.logger.info("=" * 70)
        self.logger.info("[VISUAL GEN] Total files generated: %d", len(generated_paths))
        self.logger.info("[VISUAL GEN] Target image count was: %d", target_count)
        for path in generated_paths:
            self.logger.info("[VISUAL GEN]   - %s", path.name)
        self.logger.info("=" * 70)
        
        run_paths.log(f"[Google AI] Visual generation complete: {len(generated_paths)} files generated")
        
        return generated_paths
    
    def estimate_cost(
        self,
        num_images: int = 0,
        num_videos: int = 0,
        video_seconds: int = 0,
    ) -> Dict[str, float]:
        """
        Estimate the cost of visual generation.
        
        Args:
            num_images: Number of images to generate
            num_videos: Number of videos to generate
            video_seconds: Total seconds of video
            
        Returns:
            Dict with cost breakdown and total
        """
        # Pricing (as of 2025)
        IMAGEN_COST_PER_IMAGE = 0.04  # Imagen 4 standard
        VEO_COST_PER_SECOND = 0.75  # VEO per second
        
        image_cost = num_images * IMAGEN_COST_PER_IMAGE
        video_cost = video_seconds * VEO_COST_PER_SECOND
        
        return {
            "imagen_cost": image_cost,
            "veo_cost": video_cost,
            "total_cost": image_cost + video_cost,
            "num_images": num_images,
            "video_seconds": video_seconds,
        }

    def consume_placeholder_events(self) -> List[Dict[str, str]]:
        """
        Return and clear placeholder generation events.
        
        Each event contains the output filename, sanitized prompt, and failure reason
        so that callers (e.g., the pipeline) can log meaningful warnings.
        """
        events = list(self._placeholder_events)
        self._placeholder_events.clear()
        return events

    def _record_placeholder_event(self, output_path: Path, prompt: str, reason: str) -> None:
        """Track when we had to fall back to a placeholder visual."""
        sanitized_prompt = self._sanitize_prompt_text(prompt)[:160]
        event = {
            "name": output_path.name,
            "path": str(output_path),
            "reason": reason or "Unknown Imagen error",
            "prompt": sanitized_prompt,
        }
        # Keep only recent 25 events to avoid unbounded memory use
        self._placeholder_events.append(event)
        if len(self._placeholder_events) > 25:
            self._placeholder_events = self._placeholder_events[-25:]

