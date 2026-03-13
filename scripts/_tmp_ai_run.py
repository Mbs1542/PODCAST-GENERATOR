from pathlib import Path
import json
import sys

# Ensure project root on path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils import Settings, QualityChecker
from src.utils.ai_quality_enhancer import enhance_with_ai
from src.utils.quality_checker import QualityReport, CheckResult

run_dir = Path(r"outputs/2025-12-27_הוראה-והדרכה-בטכנולוגיות-ענן")
safe = lambda s: s.encode("unicode_escape").decode("ascii")
print(f"Using run_dir: {safe(str(run_dir))}")

report_path = run_dir / "quality_report.json"
with open(report_path, "r", encoding="utf-8") as f:
    data = json.load(f)

checker = QualityChecker(Settings.load())
checker.report = QualityReport(
    timestamp=data.get("timestamp", ""),
    run_type=data.get("run_type", "FULL"),
    overall_status=data.get("overall_status", "PENDING"),
    recommendations=data.get("recommendations", []),
)
for name, check_data in data.get("postprocess", {}).items():
    checker.report.add_postprocess(
        CheckResult(
            name=name,
            status=check_data.get("status", "UNKNOWN"),
            message=check_data.get("message", ""),
            details=check_data.get("details", {}),
        )
    )

metadata = {}
meta_path = run_dir / "metadata.json"
if meta_path.exists():
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))

dialogue = {}
dialogue_path = run_dir / "dialogue.json"
if dialogue_path.exists():
    dialogue = json.loads(dialogue_path.read_text(encoding="utf-8"))

plan = enhance_with_ai(
    quality_report=checker.report,
    run_dir=run_dir,
    settings=checker.settings,
    metadata=metadata,
    dialogue=dialogue,
    apply_auto_fixes=False,
)

print("Suggestions:")
for i, s in enumerate(plan.suggestions, 1):
    line = f"{i}. [{s.severity}] {s.issue} -> {s.action} (auto-fix={s.auto_fixable})"
    print(safe(line))
print(f"Auto-fixes: {safe(str(plan.auto_fixes))}")
print(f"Manual review: {safe(str(plan.manual_review))}")

plan_path = run_dir / "enhancement_plan.json"
plan_dict = {
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
        for s in plan.suggestions
    ],
    "auto_fixes": plan.auto_fixes,
    "manual_review": plan.manual_review,
    "priority_order": plan.priority_order,
}
plan_path.write_text(json.dumps(plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Saved enhancement_plan.json to {safe(str(plan_path))}")

