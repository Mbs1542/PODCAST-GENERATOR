"""
AI-Powered Quality Enhancement System
=====================================

Uses AI (GPT/Gemini) to analyze quality reports and automatically
suggest and apply improvements to the pipeline output.

Features:
    - Analyze quality reports and identify improvement opportunities
    - Generate actionable recommendations
    - Auto-fix common issues (e.g., regenerate visuals, improve prompts)
    - Learn from historical quality patterns
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logging import get_logger

# Optional AI backends
try:
    import google.generativeai as genai  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    genai = None
try:
    from openai import AzureOpenAI  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AzureOpenAI = None
from .quality_checker import QualityReport, CheckResult


@dataclass
class EnhancementSuggestion:
    """A single AI-generated enhancement suggestion."""
    
    issue: str  # Description of the problem
    severity: str  # "critical", "high", "medium", "low"
    category: str  # "visual", "audio", "metadata", "sync", "performance"
    action: str  # What to do
    auto_fixable: bool  # Can this be fixed automatically?
    estimated_impact: str  # "high", "medium", "low"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EnhancementPlan:
    """Complete plan for enhancing pipeline output based on quality report."""
    
    suggestions: List[EnhancementSuggestion] = field(default_factory=list)
    auto_fixes: List[str] = field(default_factory=list)  # Actions that can be auto-applied
    manual_review: List[str] = field(default_factory=list)  # Actions requiring human review
    priority_order: List[int] = field(default_factory=list)  # Indices of suggestions in priority order


class AIQualityEnhancer:
    """
    AI-powered system to analyze quality reports and suggest/enact improvements.
    
    Uses GPT/Gemini to:
    1. Analyze quality reports and identify patterns
    2. Generate actionable improvement suggestions
    3. Prioritize fixes by impact
    4. Auto-apply safe fixes when possible
    """
    
    def __init__(self, settings: Any, ai_client: Any = None) -> None:
        """
        Initialize the AI Quality Enhancer.
        
        Args:
            settings: Application Settings object
            ai_client: Optional AI client (GPT/Gemini). If None, will use settings to create one.
        """
        self.settings = settings
        self.logger = get_logger(self.__class__.__name__)
        self.ai_client = ai_client or self._create_ai_client()
        
    def _create_ai_client(self) -> Any:
        """Create AI client based on settings."""
        # Try to use existing chat session if available
        try:
            from ..metadata.chat import MetadataChatSession
            session = MetadataChatSession(self.settings)
            # Wrap to provide _chat_completion interface
            return session
        except Exception as exc:
            self.logger.warning("Could not create AI client: %s", exc)
            return None
    
    def analyze_quality_report(
        self,
        quality_report: QualityReport,
        run_dir: Path,
        metadata: Optional[Dict] = None,
        dialogue: Optional[Dict] = None,
    ) -> EnhancementPlan:
        """
        Analyze a quality report and generate an enhancement plan.
        
        Args:
            quality_report: The quality report to analyze
            run_dir: Path to the run directory
            metadata: Optional metadata dict
            dialogue: Optional dialogue dict
            
        Returns:
            EnhancementPlan with suggestions and auto-fixes
        """
        if not self.ai_client:
            self.logger.warning("AI client not available, skipping AI analysis")
            return EnhancementPlan()
        
        self.logger.info("[AIQualityEnhancer] Analyzing quality report with AI...")
        
        # Prepare context for AI
        report_dict = quality_report.to_dict()
        context = self._build_analysis_context(report_dict, run_dir, metadata, dialogue)
        
        # Generate AI analysis
        try:
            analysis_prompt = self._build_analysis_prompt(context)
            response = self._query_ai(analysis_prompt)
            if not response.strip():
                self.logger.warning("[AIQualityEnhancer] AI returned empty response; using fallback plan.")
                return self._build_fallback_plan(context)
            plan = self._parse_ai_response(response, quality_report)
            # If parsing failed or returned nothing, fallback
            if not plan.suggestions and not plan.auto_fixes and not plan.manual_review:
                self.logger.warning("[AIQualityEnhancer] AI plan empty; using fallback plan.")
                return self._build_fallback_plan(context)
            return plan
        except Exception as exc:
            self.logger.error("AI analysis failed: %s", exc, exc_info=True)
            return EnhancementPlan()
    
    def _build_analysis_context(
        self,
        report_dict: Dict,
        run_dir: Path,
        metadata: Optional[Dict],
        dialogue: Optional[Dict],
    ) -> Dict[str, Any]:
        """Build context dictionary for AI analysis."""
        context = {
            "quality_report": report_dict,
            "run_dir": str(run_dir),
            "overall_status": report_dict.get("overall_status", "UNKNOWN"),
        }
        
        # Extract key issues
        issues = []
        warnings = []
        
        for check_type in ["preflight", "postprocess"]:
            checks = report_dict.get(check_type, {})
            for check_name, check_data in checks.items():
                status = check_data.get("status", "UNKNOWN")
                message = check_data.get("message", "")
                details = check_data.get("details", {})
                
                if status == "FAIL":
                    issues.append({
                        "type": check_type,
                        "name": check_name,
                        "message": message,
                        "details": details,
                    })
                elif status == "WARNING":
                    warnings.append({
                        "type": check_type,
                        "name": check_name,
                        "message": message,
                        "details": details,
                    })
        
        context["critical_issues"] = issues
        context["warnings"] = warnings
        context["recommendations"] = report_dict.get("recommendations", [])
        
        # Add metadata context if available
        if metadata:
            context["metadata"] = {
                "topic": metadata.get("topic", ""),
                "key_concepts": metadata.get("key_concepts", []),
                "language": metadata.get("language", "he"),
            }
        
        # Add dialogue context if available
        if dialogue:
            dialogue_entries = dialogue.get("dialogue", [])
            context["dialogue"] = {
                "entry_count": len(dialogue_entries),
                "speakers": list(set(e.get("speaker", "") for e in dialogue_entries)),
            }
        
        return context

    def _build_fallback_plan(self, context: Dict[str, Any]) -> EnhancementPlan:
        """Build a basic plan from quality report issues/warnings when AI is unavailable."""
        suggestions: List[EnhancementSuggestion] = []
        # Build suggestions from critical issues
        for issue in context.get("critical_issues", []):
            suggestions.append(
                EnhancementSuggestion(
                    issue=issue.get("message", issue.get("name", "Critical issue")),
                    severity="critical",
                    category=issue.get("name", "other"),
                    action="Investigate and rerun pipeline; see quality report details.",
                    auto_fixable=False,
                    estimated_impact="high",
                    details=issue.get("details", {}),
                )
            )
        # Build suggestions from warnings
        for warn in context.get("warnings", []):
            suggestions.append(
                EnhancementSuggestion(
                    issue=warn.get("message", warn.get("name", "Warning")),
                    severity="medium",
                    category=warn.get("name", "other"),
                    action="Review warning and adjust settings/assets accordingly.",
                    auto_fixable=False,
                    estimated_impact="medium",
                    details=warn.get("details", {}),
                )
            )
        manual_review = []
        if not suggestions:
            manual_review.append(
                "AI response unavailable or empty; manual review recommended. Check processing_log and quality_report."
            )
        return EnhancementPlan(
            suggestions=suggestions,
            auto_fixes=[],
            manual_review=manual_review,
            priority_order=list(range(len(suggestions))),
        )
    
    def _build_analysis_prompt(self, context: Dict[str, Any]) -> str:
        """Build the AI prompt for quality report analysis."""
        prompt = f"""You are an expert quality analyst for a podcast/video generation pipeline.

Analyze the following quality report and provide actionable improvement suggestions.

QUALITY REPORT SUMMARY:
- Overall Status: {context['overall_status']}
- Critical Issues: {len(context.get('critical_issues', []))}
- Warnings: {len(context.get('warnings', []))}
- Existing Recommendations: {len(context.get('recommendations', []))}

CRITICAL ISSUES:
{json.dumps(context.get('critical_issues', []), indent=2, ensure_ascii=False)}

WARNINGS:
{json.dumps(context.get('warnings', []), indent=2, ensure_ascii=False)}

EXISTING RECOMMENDATIONS:
{json.dumps(context.get('recommendations', []), indent=2, ensure_ascii=False)}

METADATA CONTEXT:
{json.dumps(context.get('metadata', {}), indent=2, ensure_ascii=False)}

DIALOGUE CONTEXT:
{json.dumps(context.get('dialogue', {}), indent=2, ensure_ascii=False)}

TASK:
Analyze this quality report and provide a structured JSON response with:
1. Specific improvement suggestions for each issue/warning
2. Priority ranking (critical > high > medium > low)
3. Whether each fix can be automated
4. Estimated impact of each fix

For each suggestion, provide:
- issue: Clear description of the problem
- severity: "critical", "high", "medium", or "low"
- category: "visual", "audio", "metadata", "sync", "performance", or "other"
- action: Specific actionable step to fix
- auto_fixable: true/false
- estimated_impact: "high", "medium", or "low"
- details: Additional context

Focus on:
- Visual quality issues (missing images, corrupted files, poor prompts)
- Audio sync problems
- Metadata completeness
- Performance optimizations
- Hebrew/RTL text rendering issues

Return ONLY valid JSON in this format:
{{
  "suggestions": [
    {{
      "issue": "Description of problem",
      "severity": "critical|high|medium|low",
      "category": "visual|audio|metadata|sync|performance|other",
      "action": "Specific fix action",
      "auto_fixable": true|false,
      "estimated_impact": "high|medium|low",
      "details": {{}}
    }}
  ],
  "priority_order": [0, 1, 2, ...],
  "auto_fixes": ["action1", "action2", ...],
  "manual_review": ["action1", "action2", ...]
}}
"""
        return prompt
    
    def _query_ai(self, prompt: str) -> str:
        """Query AI (Azure OpenAI first, fallback to Gemini)."""
        # Prefer Azure OpenAI
        if AzureOpenAI and self.settings.openai_api_key and self.settings.openai_endpoint:
            try:
                client = AzureOpenAI(
                    api_key=self.settings.openai_api_key,
                    api_version=self.settings.openai_api_version,
                    azure_endpoint=self.settings.openai_endpoint,
                )
                model = getattr(self.settings, "openai_deployment", "")
                if not model:
                    self.logger.warning("openai_deployment is empty; cannot query Azure OpenAI.")
                else:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        max_tokens=800,
                    )
                    return (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                self.logger.warning("Azure OpenAI call failed: %s", exc, exc_info=True)
        else:
            self.logger.debug("Azure OpenAI not configured; skipping.")

        # Fallback to Gemini
        if genai and getattr(self.settings, "gemini_api_key", ""):
            try:
                genai.configure(api_key=self.settings.gemini_api_key)
                model_name = getattr(self.settings, "gemini_model", "") or "gemini-1.5-flash"
                model = genai.GenerativeModel(model_name)
                resp = model.generate_content(prompt, generation_config={"temperature": 0.3})
                text = getattr(resp, "text", "") or ""
                return text.strip()
            except Exception as exc:
                self.logger.warning("Gemini call failed: %s", exc, exc_info=True)
        else:
            self.logger.debug("Gemini not configured; skipping.")

        self.logger.warning("[AIQualityEnhancer] Empty AI response (no providers succeeded)")
        return ""
    
    def _parse_ai_response(self, response: str, quality_report: QualityReport) -> EnhancementPlan:
        """Parse AI response into EnhancementPlan."""
        try:
            # Extract JSON from response (may have markdown fences)
            response = response.strip()
            if not response:
                self.logger.warning("[AIQualityEnhancer] No AI response to parse.")
                return EnhancementPlan(manual_review=["AI returned empty response; check API keys or model settings"])
            if response.startswith("```"):
                # Remove markdown code fences
                lines = response.split("\n")
                start_idx = 1 if lines[0].startswith("```") else 0
                end_idx = -1 if lines[-1].startswith("```") else len(lines)
                response = "\n".join(lines[start_idx:end_idx])
            
            data = json.loads(response)
            
            suggestions = []
            for sug_dict in data.get("suggestions", []):
                suggestions.append(EnhancementSuggestion(
                    issue=sug_dict.get("issue", ""),
                    severity=sug_dict.get("severity", "medium"),
                    category=sug_dict.get("category", "other"),
                    action=sug_dict.get("action", ""),
                    auto_fixable=sug_dict.get("auto_fixable", False),
                    estimated_impact=sug_dict.get("estimated_impact", "medium"),
                    details=sug_dict.get("details", {}),
                ))
            
            plan = EnhancementPlan(
                suggestions=suggestions,
                auto_fixes=data.get("auto_fixes", []),
                manual_review=data.get("manual_review", []),
                priority_order=data.get("priority_order", list(range(len(suggestions)))),
            )
            
            self.logger.info(
                "[AIQualityEnhancer] Generated %d suggestions (%d auto-fixable)",
                len(suggestions),
                len(plan.auto_fixes),
            )
            
            return plan
            
        except Exception as exc:
            self.logger.error(
                "Failed to parse AI response: %s | raw: %s",
                exc,
                response[:500],
                exc_info=True,
            )
            return EnhancementPlan(
                manual_review=["AI response was invalid JSON; check logs and model configuration"]
            )
    
    def apply_auto_fixes(
        self,
        plan: EnhancementPlan,
        run_dir: Path,
        quality_report: QualityReport,
    ) -> Dict[str, Any]:
        """
        Apply automatic fixes from the enhancement plan.
        
        Args:
            plan: The enhancement plan
            run_dir: Path to run directory
            quality_report: Original quality report
            
        Returns:
            Dict with results of auto-fixes
        """
        results = {
            "applied": [],
            "failed": [],
            "skipped": [],
        }
        
        for fix_action in plan.auto_fixes:
            try:
                self.logger.info("[AIQualityEnhancer] Applying auto-fix: %s", fix_action)
                result = self._apply_single_fix(fix_action, run_dir, quality_report)
                if result["success"]:
                    results["applied"].append(fix_action)
                else:
                    results["failed"].append({"action": fix_action, "error": result.get("error")})
            except Exception as exc:
                self.logger.error("Auto-fix failed for %s: %s", fix_action, exc)
                results["failed"].append({"action": fix_action, "error": str(exc)})
        
        return results
    
    def _apply_single_fix(self, action: str, run_dir: Path, quality_report: QualityReport) -> Dict[str, Any]:
        """Apply a single auto-fix action."""
        # Parse action type and parameters
        action_lower = action.lower()
        
        # Visual-related fixes
        if "regenerate" in action_lower and "image" in action_lower:
            return self._regenerate_images(run_dir, quality_report)
        elif "improve" in action_lower and "prompt" in action_lower:
            return self._improve_visual_prompts(run_dir, quality_report)
        elif "fix" in action_lower and "sync" in action_lower:
            return self._fix_caption_sync(run_dir, quality_report)
        
        # Audio-related fixes
        elif "normalize" in action_lower and "audio" in action_lower:
            return self._normalize_audio(run_dir, quality_report)
        
        # Metadata fixes
        elif "complete" in action_lower and "metadata" in action_lower:
            return self._complete_metadata(run_dir, quality_report)
        
        # Default: not implemented
        return {"success": False, "error": "Fix action not implemented"}
    
    def _regenerate_images(self, run_dir: Path, quality_report: QualityReport) -> Dict[str, Any]:
        """Regenerate corrupted or missing images."""
        # This would integrate with GoogleAIVisualGenerator
        # For now, return placeholder
        return {"success": False, "error": "Image regeneration not yet implemented"}
    
    def _improve_visual_prompts(self, run_dir: Path, quality_report: QualityReport) -> Dict[str, Any]:
        """Improve visual metadata prompts based on quality issues."""
        # This would use AI to rewrite prompts in visual_metadata.json
        return {"success": False, "error": "Prompt improvement not yet implemented"}
    
    def _fix_caption_sync(self, run_dir: Path, quality_report: QualityReport) -> Dict[str, Any]:
        """Fix caption synchronization issues."""
        # This would regenerate captions.srt with proper timing
        return {"success": False, "error": "Caption sync fix not yet implemented"}
    
    def _normalize_audio(self, run_dir: Path, quality_report: QualityReport) -> Dict[str, Any]:
        """Normalize audio levels."""
        # This would use ProductionGuardian or similar
        return {"success": False, "error": "Audio normalization not yet implemented"}
    
    def _complete_metadata(self, run_dir: Path, quality_report: QualityReport) -> Dict[str, Any]:
        """Complete missing metadata fields."""
        # This would use MetadataChatSession to fill gaps
        return {"success": False, "error": "Metadata completion not yet implemented"}


def enhance_with_ai(
    quality_report: QualityReport,
    run_dir: Path,
    settings: Any,
    metadata: Optional[Dict] = None,
    dialogue: Optional[Dict] = None,
    apply_auto_fixes: bool = False,
) -> EnhancementPlan:
    """
    Convenience function to analyze and optionally enhance a quality report with AI.
    
    Args:
        quality_report: The quality report to analyze
        run_dir: Path to run directory
        settings: Application settings
        metadata: Optional metadata dict
        dialogue: Optional dialogue dict
        apply_auto_fixes: Whether to automatically apply safe fixes
        
    Returns:
        EnhancementPlan with suggestions
    """
    enhancer = AIQualityEnhancer(settings)

    # Guardrail: ensure required assets exist before invoking AI
    available_videos = list(run_dir.glob("*.mp4"))
    available_images = list(run_dir.glob("*.png")) + list(run_dir.glob("*.jpg")) + list(run_dir.glob("*.jpeg"))
    if not available_videos and not available_images:
        enhancer.logger.warning("[AIQualityEnhancer] No visual/video assets found; skipping AI enhancement.")
        skipped_plan = EnhancementPlan(
            manual_review=["Skipped AI enhancement: no assets found in run_dir"],
            suggestions=[],
            auto_fixes=[],
            priority_order=[],
        )
        _write_ai_report(run_dir, skipped_plan, reason="no_assets")
        return skipped_plan

    plan = enhancer.analyze_quality_report(quality_report, run_dir, metadata, dialogue)
    
    if apply_auto_fixes and plan.auto_fixes:
        results = enhancer.apply_auto_fixes(plan, run_dir, quality_report)
        plan.details = {"auto_fix_results": results}
    
    _write_ai_report(run_dir, plan, reason=None)
    return plan


def _write_ai_report(run_dir: Path, plan: EnhancementPlan, reason: Optional[str]) -> None:
    """
    Persist a small JSON report describing AI enhancement actions/decisions.
    """
    report = {
        "reason": reason,
        "suggestions": [
            {
                "issue": s.issue,
                "severity": s.severity,
                "category": s.category,
                "action": s.action,
                "auto_fixable": s.auto_fixable,
                "estimated_impact": s.estimated_impact,
                "details": s.details,
            }
            for s in getattr(plan, "suggestions", []) or []
        ],
        "auto_fixes": getattr(plan, "auto_fixes", []),
        "manual_review": getattr(plan, "manual_review", []),
        "priority_order": getattr(plan, "priority_order", []),
    }
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_dir / "ai_enhancement_report.json"
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # best effort; do not raise
        pass

