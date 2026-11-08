"""
Tests for GoogleAIVisualGenerator module.

Tests cover image generation, video generation, and cost estimation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock, patch, Mock

import pytest


class TestGoogleAIVisualGeneratorInit:
    """Tests for GoogleAIVisualGenerator initialization."""
    
    def test_init_without_api_key(self):
        """Test initialization when no API key is configured."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = ""  # No API key
        
        generator = GoogleAIVisualGenerator(settings=mock_settings)
        
        assert not generator.is_available
    
    def test_init_with_api_key(self):
        """Test initialization when API key is configured."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = "test-api-key"
        mock_settings.imagen_model = "imagen-4.0-generate-001"
        mock_settings.veo_model = "veo-2.0-generate-001"
        
        with patch('google.genai.Client') as mock_client:
            GoogleAIVisualGenerator(settings=mock_settings)

            # The google-genai client is created with the API key
            mock_client.assert_called_once_with(api_key="test-api-key")


class TestCostEstimation:
    """Tests for cost estimation functionality."""
    
    def test_estimate_cost_images_only(self):
        """Test cost estimation for image generation only."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = ""  # Doesn't need API for cost estimation
        
        generator = GoogleAIVisualGenerator(settings=mock_settings)
        
        cost = generator.estimate_cost(num_images=5, num_videos=0, video_seconds=0)
        
        assert cost["num_images"] == 5
        assert cost["imagen_cost"] == 0.20  # 5 * $0.04
        assert cost["veo_cost"] == 0.0
        assert cost["total_cost"] == 0.20
    
    def test_estimate_cost_videos_only(self):
        """Test cost estimation for video generation only."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = ""
        
        generator = GoogleAIVisualGenerator(settings=mock_settings)
        
        cost = generator.estimate_cost(num_images=0, num_videos=1, video_seconds=10)
        
        assert cost["video_seconds"] == 10
        assert cost["imagen_cost"] == 0.0
        assert cost["veo_cost"] == 7.50  # 10 * $0.75
        assert cost["total_cost"] == 7.50
    
    def test_estimate_cost_combined(self):
        """Test cost estimation for combined image and video generation."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = ""
        
        generator = GoogleAIVisualGenerator(settings=mock_settings)
        
        cost = generator.estimate_cost(num_images=3, num_videos=1, video_seconds=5)
        
        assert cost["imagen_cost"] == 0.12  # 3 * $0.04
        assert cost["veo_cost"] == 3.75  # 5 * $0.75
        assert cost["total_cost"] == 3.87


class TestPlaceholderGeneration:
    """Tests for placeholder image generation when API is unavailable."""
    
    def test_generate_placeholder_image(self):
        """Test placeholder image generation."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = ""  # No API - will use placeholder
        
        generator = GoogleAIVisualGenerator(settings=mock_settings)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "placeholder.png"
            
            generator._generate_placeholder_image(
                prompt="Test network diagram",
                output_path=output_path,
                aspect_ratio="16:9"
            )
            
            # File should be created
            assert output_path.exists()
            
            # File should have reasonable size (not empty)
            assert output_path.stat().st_size > 1000  # At least 1KB
    
    def test_placeholder_aspect_ratios(self):
        """Test placeholder generation with different aspect ratios."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings
        from PIL import Image
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = ""
        
        generator = GoogleAIVisualGenerator(settings=mock_settings)
        
        test_cases = [
            ("16:9", 1920, 1080),
            ("1:1", 1080, 1080),
            ("4:3", 1440, 1080),
        ]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            for aspect_ratio, expected_width, expected_height in test_cases:
                output_path = Path(tmpdir) / f"placeholder_{aspect_ratio.replace(':', '_')}.png"
                
                generator._generate_placeholder_image(
                    prompt="Test",
                    output_path=output_path,
                    aspect_ratio=aspect_ratio
                )
                
                # Verify dimensions
                with Image.open(output_path) as img:
                    assert img.width == expected_width
                    assert img.height == expected_height


class TestVisualBuilding:
    """Tests for building visuals from dialogue."""
    
    def test_build_visuals_when_unavailable(self):
        """Test that empty list is returned when API is unavailable."""
        from src.visuals.google_ai_visuals import GoogleAIVisualGenerator
        from src.utils import Settings, RunPaths
        
        mock_settings = MagicMock(spec=Settings)
        mock_settings.gemini_api_key = ""  # No API
        
        generator = GoogleAIVisualGenerator(settings=mock_settings)
        
        dialogue_json = {"dialogue": [{"speaker": "A", "text": "Hello"}]}
        metadata = {"topic": "Test", "key_concepts": ["A", "B", "C"]}
        mock_run_paths = MagicMock(spec=RunPaths)
        
        result = generator.build_visuals_for_dialogue(dialogue_json, metadata, mock_run_paths)
        
        assert result == []


class TestColorSchemes:
    """Tests for topic-based color scheme selection."""
    
    def test_azure_color_scheme(self):
        """Test that azure topics get blue color scheme."""
        from src.visuals.google_ai_visuals import TOPIC_COLOR_SCHEMES
        
        assert "azure" in TOPIC_COLOR_SCHEMES
        assert "blue" in TOPIC_COLOR_SCHEMES["azure"].lower()
    
    def test_default_color_scheme(self):
        """Test that default color scheme exists."""
        from src.visuals.google_ai_visuals import TOPIC_COLOR_SCHEMES
        
        assert "default" in TOPIC_COLOR_SCHEMES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

