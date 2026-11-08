from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from jsonschema import Draft7Validator, ValidationError
from openai import APIError, APITimeoutError, AzureOpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..utils import CostTracker, RunPaths, Settings, get_logger
from .chunker import TokenChunker


DIALOGUE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "dialogue": {
            "type": "array",
            "minItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["Roee", "Noa"]},
                    "text": {"type": "string", "minLength": 1},
                },
                "required": ["speaker", "text"],
            },
        },
        "metadata": {
            "type": "object",
            "properties": {
                "total_exchanges": {"type": "integer", "minimum": 10},
                "estimated_duration_minutes": {"type": "number", "minimum": 5},
                "key_topics": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["total_exchanges", "estimated_duration_minutes", "key_topics"],
        },
    },
    "required": ["dialogue", "metadata"],
}


@dataclass
class DialogueGenerator:
    """Generate podcast dialogues from raw transcripts using Azure OpenAI."""

    settings: Settings
    cost_tracker: Optional[CostTracker] = None
    max_dialogue_tokens: int = 2000

    def __post_init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        # The legacy single-prompt path needs Azure; the v2 engine uses whichever writer is configured
        self.client = AzureOpenAI(
            api_key=self.settings.openai_api_key,
            api_version=self.settings.openai_api_version,
            azure_endpoint=self.settings.openai_endpoint,
        ) if self.settings.openai_endpoint else None
        self.token_chunker = TokenChunker()
        self._validator = Draft7Validator(DIALOGUE_SCHEMA)
        if self.cost_tracker is None:
            self.cost_tracker = CostTracker()

    def _target_language(self) -> str:
        lang = getattr(self.settings, "target_language", "") or ""
        return "en" if str(lang).lower().startswith("en") else "he"

    def generate(
        self,
        transcript_text: str,
        metadata: Dict[str, Any],
        run_paths: RunPaths,
        force: bool = False,
    ) -> Path:
        """Generate or load cached dialogue JSON."""
        run_paths.log("Starting dialogue generation.")
        if str(getattr(self.settings, "dialogue_engine", "v2")).lower() == "v2":
            try:
                return self._generate_v2(transcript_text, metadata, run_paths, force)
            except Exception as exc:
                if self.client is None:
                    raise  # no Azure for the legacy fallback: report the engine's own error
                self.logger.exception("Script engine v2 failed; falling back to the legacy generator.")
                run_paths.log(f"Script engine v2 failed ({exc}); falling back to the legacy generator.")

        content_hash = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
        cache_file = run_paths.cache_dir / f"{content_hash}.json"

        if self.settings.cache_dialogues and cache_file.exists() and not force:
            self.logger.info("Loading dialogue from cache.")
            cache_text = cache_file.read_text(encoding="utf-8")
            run_paths.dialogue_path.write_text(cache_text, encoding="utf-8")
            return run_paths.dialogue_path

        condensed_content = self._prepare_content(transcript_text)
        prompt_messages = self._build_prompt(condensed_content, metadata)
        response = self._chat_completion(prompt_messages, self.max_dialogue_tokens, response_format={"type": "json_object"})
        payload = self._parse_and_validate(response)

        run_paths.dialogue_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.settings.cache_dialogues:
            cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        run_paths.log("Dialogue generation completed.")
        return run_paths.dialogue_path

    def _generate_v2(
        self,
        transcript_text: str,
        metadata: Dict[str, Any],
        run_paths: RunPaths,
        force: bool,
    ) -> Path:
        """Staged engine: clean -> lesson profile -> episode plan -> segment writing + verification."""
        from ..engine import ScriptEngine

        engine = ScriptEngine(self.settings, self.cost_tracker, log=run_paths.log)
        result = engine.run(transcript_text, metadata, run_paths.run_dir / "engine", force=force)
        run_paths.dialogue_path.write_text(json.dumps(result.dialogue, ensure_ascii=False, indent=2), encoding="utf-8")
        quality = result.dialogue["metadata"]["quality"]
        run_paths.log(
            f"Dialogue generation completed (engine v2): {quality['estimated_minutes']} min, "
            f"core concept coverage {quality['core_concept_coverage']:.0%}, "
            f"segments with open issues: {quality['segments_with_open_issues'] or 'none'}"
        )
        return run_paths.dialogue_path

    def _prepare_content(self, transcript_text: str) -> str:
        chunks = list(self.token_chunker.chunk(transcript_text))
        if not chunks:
            raise ValueError("Transcript is empty or malformed.")
        if len(chunks) == 1:
            return chunks[0]

        self.logger.info("Transcript requires summarization across %s chunks.", len(chunks))
        summaries = []
        for idx, chunk in enumerate(chunks, start=1):
            summary_prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are a teaching assistant summarizing lectures concisely in English."
                        if self._target_language() == "en"
                        else "אתה עוזר לימודי שמסכם הרצאות ותוכן לימודי בצורה תמציתית בעברית."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Summarize part {idx}/{len(chunks)} of the lecture, highlighting key ideas, examples, and takeaways:\n\n{chunk}"
                        if self._target_language() == "en"
                        else f"סכם את החלק {idx}/{len(chunks)} של ההרצאה, הדגש רעיונות מרכזיים, דוגמאות ונקודות מפתח:\n\n{chunk}"
                    ),
                },
            ]
            response = self._chat_completion(summary_prompt, 800)
            summaries.append(response.choices[0].message.content.strip())
        return "\n\n".join(summaries)

    def _build_prompt(self, condensed_transcript: str, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        """Build universal dialogue prompt that works for ANY educational content."""
        return self._build_universal_prompt(condensed_transcript, metadata)

    def _build_universal_prompt(self, condensed_transcript: str, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        """Build universal dialogue prompt that works for ANY educational content."""
        topic = metadata.get("topic", "נושא כללי")
        key_concepts = metadata.get("key_concepts", [])
        labs = metadata.get("labs", [])
        summary = metadata.get("summary", "")

        # Classify content type for appropriate dialogue style
        content_type = self._classify_content_type(metadata)

        # Generate concept explanations based on content type
        concept_explanations = self._generate_concept_explanations(key_concepts, content_type)

        # Generate appropriate dialogue structure
        dialogue_structure = self._generate_dialogue_structure(content_type, len(key_concepts))

        if self._target_language() == "en":
            prompt_body = f"""You create a friendly educational podcast in English for any learning content.

Content details:
- Topic: {topic}
- Content type: {content_type}
- Key concepts: {", ".join(key_concepts[:5])}
- Summary: {summary[:200] + "..." if len(summary) > 200 else summary}

Dialogue style for {content_type}:
{dialogue_structure}

Explaining concepts:
{concept_explanations}

Source content:
{condensed_transcript}

General guidelines:
- Write a natural English conversation between Roee (expert host) and Noa (curious learner).
- Focus only on the provided content; avoid external information.
- Explain concepts clearly with relevant examples.
- Align tone to the content type (technical, business, science, creative, educational).
- End with 3 key takeaways.

Return JSON:
{{
  "dialogue": [{{"speaker": "Roee", "text": ""}}, ...],
  "metadata": {{
    "total_exchanges": <int>,
    "estimated_duration_minutes": <number>,
    "key_topics": [<string>, ...]
  }}
}}
"""
            system_prompt = "You are an expert at creating educational podcast dialogues in English for any content type."
        else:
            prompt_body = f"""אתה יוצר פודקאסט חינוכי ידידותי בעברית שמתאים לכל סוג תוכן לימודי.

פרטי התוכן:
- נושא: {topic}
- סוג תוכן: {content_type}
- מושגים מרכזיים: {", ".join(key_concepts[:5])}
- תקציר: {summary[:200] + "..." if len(summary) > 200 else summary}

סגנון דיאלוג מתאים ל{content_type}:
{dialogue_structure}

הסברת מושגים:
{concept_explanations}

תוכן המקור:
{condensed_transcript}

הנחיות כלליות:
- כתוב שיחה טבעית בעברית בין Roee (המנחה המומחה) ל-Noa (הסקרנית הלומדת)
- התמקד אך ורק בתוכן שסופק - אל תוסיף מידע חיצוני
- הסבר מושגים בצורה ברורה עם דוגמאות רלוונטיות
- התאם את שפת הדיאלוג לסוג התוכן (טכני, עסקי, מדעי, יצירתי)
- סיים ב-3 נקודות מפתח לסיכום

החזר JSON במבנה:
{{
  "dialogue": [{{"speaker": "Roee", "text": ""}}, ...],
  "metadata": {{
    "total_exchanges": <int>,
    "estimated_duration_minutes": <number>,
    "key_topics": [<string>, ...]
  }}
}}
"""
            system_prompt = "אתה מומחה ביצירת פודקאסטים חינוכיים בעברית לכל סוגי התכנים."

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_body.strip()}
        ]

    def _classify_content_type(self, metadata: Dict) -> str:
        """Classify content type for dialogue adaptation."""
        # Use the centralized content classifier
        from ..utils.content_classifier import ContentClassifier
        classifier = ContentClassifier()
        return classifier.classify(metadata)

    def _generate_concept_explanations(self, concepts: List[str], content_type: str) -> str:
        """Generate appropriate concept explanation guidelines."""
        explanations = {
            "technical_programming": "הסבר קוד ומושגים טכניים עם דוגמאות קוד קצרות",
            "technical_networking": "הסבר מושגי רשת עם אנלוגיות יומיומיות",
            "business_professional": "הסבר מושגים עסקיים עם דוגמאות מהחיים",
            "science_medical": "הסבר מושגים מדעיים בצורה מדויקת ומובנת",
            "creative_artistic": "הסבר מושגים יצירתיים עם השראה והמחשה",
            "educational_general": "הסבר מושגים בצורה ברורה ויסודית"
        }

        base_explanation = explanations.get(content_type, explanations["educational_general"])

        return f"""איך להסביר את המושגים ל{content_type}:
- {base_explanation}
- התחל מהבסיס ותבנה בהדרגה למושגים מורכבים יותר
- השתמש באנלוגיות מתאימות לסוג התוכן
- הסבר את החשיבות הפרקטית של כל מושג"""

    def _generate_dialogue_structure(self, content_type: str, num_concepts: int) -> str:
        """Generate appropriate dialogue structure for content type."""
        structures = {
            "technical_programming": """- Roee: מסביר קוד ומתכנת עם דוגמאות קונקרטיות
- Noa: שואלת על שגיאות נפוצות ויישומים פרקטיים
- התמקד בבניית הבנה הדרגתית של מושגים טכניים""",

            "technical_networking": """- Roee: מסביר ארכיטקטורות רשת עם דיאגרמות מילוליות
- Noa: שואלת על תרחישים אמיתיים ופתרון בעיות
- התמקד בהבנת זרימת הנתונים והתצורה""",

            "business_professional": """- Roee: מסביר אסטרטגיות עסקיות עם דוגמאות מהשוק
- Noa: שואלת על יישום מעשי והשפעה על הצלחה
- התמקד בהבנת ההיגיון העסקי והערך המוסף""",

            "science_medical": """- Roee: מסביר תהליכים מדעיים בצורה מדויקת
- Noa: מבקשת הבהרות על מושגים מורכבים
- התמקד בראיות מדעיות ויישומים מעשיים""",

            "creative_artistic": """- Roee: מדגים תהליכים יצירתיים עם דוגמאות
- Noa: מביעה התלהבות ושואלת על השראה
- התמקד בחוויה האמנותית והביטוי האישי""",

            "educational_general": """- Roee: מסביר מושגים בצורה שיטתית עם דוגמאות
- Noa: מבקשת הבהרות ושואלת על יישומים
- התמקד בבניית הבנה יסודית ונכונה"""
        }

        return structures.get(content_type, structures["educational_general"])

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type((RateLimitError, APIError, APITimeoutError)),
        reraise=True,
    )
    def _chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        response_format: Optional[Dict[str, str]] = None,
    ):
        response = self.client.chat.completions.create(
            model=self.settings.openai_deployment,
            messages=messages,
            temperature=0.45,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        if response.usage and self.cost_tracker:
            self.cost_tracker.add_openai_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
        return response

    def _parse_and_validate(self, response) -> Dict[str, Any]:
        content = response.choices[0].message.content
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            self.logger.error("Model returned invalid JSON: %s", content[:200])
            raise ValueError("Dialogue JSON is invalid") from exc
        try:
            self._validator.validate(payload)
        except ValidationError as exc:
            raise ValueError(f"Dialogue JSON failed validation: {exc.message}") from exc
        return payload

