"""Build a product confirmation checklist from a requirement review."""

from datetime import datetime, timezone
from uuid import uuid4

from .schemas import (
    ArtifactMeta,
    ArtifactStatus,
    ProductConfirmationChecklist,
    ProductConfirmationItem,
    RequirementReview,
)


ARTIFACT_TYPE = "product_confirmation_checklist"


def build_confirmation_checklist(
    review: RequirementReview,
    *,
    project_id: str,
    artifact_id: str | None = None,
    version: int = 1,
    created_at: datetime | None = None,
    now: datetime | None = None,
) -> ProductConfirmationChecklist:
    """Create a deterministic checklist without invoking an AI model."""

    generated_at = now or datetime.now(timezone.utc)
    items: list[ProductConfirmationItem] = []

    for index, issue in enumerate(review.issues):
        question = (
            review.open_questions[index]
            if index < len(review.open_questions)
            else f"请确认该问题的正式产品规则：{issue.description}"
        )
        items.append(
            ProductConfirmationItem(
                item_id=f"CHK-{index + 1:03d}",
                source_issue_id=issue.issue_id,
                severity=issue.severity,
                location=issue.location,
                question=question,
                problem=issue.description,
                impact=issue.impact,
                suggestion=issue.suggestion,
            )
        )

    for question in review.open_questions[len(review.issues) :]:
        item_number = len(items) + 1
        items.append(
            ProductConfirmationItem(
                item_id=f"CHK-{item_number:03d}",
                severity="medium",
                location="需求文档（待产品补充定位）",
                question=question,
                problem="需求评审中存在尚未明确的产品规则",
                impact="规则未确认可能导致实现与测试预期不一致",
                suggestion="请产品负责人补充明确、可验收的规则和示例",
            )
        )

    return ProductConfirmationChecklist(
        meta=ArtifactMeta(
            artifact_id=artifact_id or f"confirmation-{uuid4().hex}",
            artifact_type=ARTIFACT_TYPE,
            project_id=project_id,
            version=version,
            status=ArtifactStatus.PENDING,
            source_artifacts=[review.meta.artifact_id],
            created_by="butterfly-agent",
            created_at=created_at or generated_at,
            updated_at=generated_at,
        ),
        source_review_id=review.meta.artifact_id,
        source_review_version=review.meta.version,
        items=items,
    )


def render_confirmation_checklist(
    checklist: ProductConfirmationChecklist,
) -> str:
    """Render the checklist as a human-readable Markdown document."""

    lines = [
        "# 产品确认清单",
        "",
        f"- 清单版本：v{checklist.meta.version}",
        (
            "- 来源需求评审："
            f"{checklist.source_review_id} v{checklist.source_review_version}"
        ),
        f"- 待确认项：{len(checklist.items)} 项",
        f"- 生成时间：{checklist.meta.updated_at.isoformat()}",
        "",
    ]
    if not checklist.items:
        lines.extend(["当前需求评审没有待确认事项。", ""])
        return "\n".join(lines)

    for item in checklist.items:
        lines.extend(
            [
                f"## {item.item_id} · {_severity_label(item.severity)}",
                "",
                "- [ ] 待产品确认",
                f"- 来源问题：{item.source_issue_id or '独立开放问题'}",
                f"- 定位：{item.location}",
                f"- 待确认问题：{item.question}",
                f"- 问题：{item.problem}",
                f"- 影响：{item.impact}",
                f"- 建议：{item.suggestion}",
                "",
            ]
        )
    return "\n".join(lines)


def _severity_label(severity: str) -> str:
    return {
        "blocker": "阻断",
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
    }[severity]
