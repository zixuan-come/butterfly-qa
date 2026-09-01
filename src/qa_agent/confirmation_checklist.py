"""Build a product decision checklist from a requirement review."""

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
_DEFAULT_OPTIONS = [
    "接受建议并补充为正式产品规则",
    "不采纳建议，并填写替代产品规则",
]


def build_confirmation_checklist(
    review: RequirementReview,
    *,
    project_id: str,
    artifact_id: str | None = None,
    version: int = 1,
    created_at: datetime | None = None,
    now: datetime | None = None,
) -> ProductConfirmationChecklist:
    """Create product decision tasks without invoking an AI model."""

    generated_at = now or datetime.now(timezone.utc)
    items: list[ProductConfirmationItem] = []
    confirmable_issues = [
        (index, issue)
        for index, issue in enumerate(review.issues)
        if issue.needs_product_confirmation
    ]
    consumed_question_indexes: set[int] = set()

    for item_index, (issue_index, issue) in enumerate(confirmable_issues):
        question = (
            review.open_questions[issue_index]
            if issue_index < len(review.open_questions)
            else f"请确认该问题的正式产品规则：{issue.description}"
        )
        if issue_index < len(review.open_questions):
            consumed_question_indexes.add(issue_index)
        items.append(
            ProductConfirmationItem(
                item_id=f"CHK-{item_index + 1:03d}",
                source_issue_id=issue.issue_id,
                severity=issue.severity,
                location=issue.location,
                question=question,
                decision_options=list(_DEFAULT_OPTIONS),
            )
        )

    remaining_questions = [
        question
        for index, question in enumerate(review.open_questions)
        if index not in consumed_question_indexes
    ]
    for question in remaining_questions:
        item_number = len(items) + 1
        items.append(
            ProductConfirmationItem(
                item_id=f"CHK-{item_number:03d}",
                source_issue_id=None,
                severity="medium",
                location="需求文档（待产品补充定位）",
                question=question,
                decision_options=list(_DEFAULT_OPTIONS),
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
    """Render only the product-facing decision fields as Markdown."""

    lines = [
        "# 产品确认清单",
        "",
        f"- 清单版本：v{checklist.meta.version}",
        (
            "- 来源需求评审："
            f"{checklist.source_review_id} v{checklist.source_review_version}"
        ),
        f"- 待产品决策：{len(checklist.items)} 项",
        f"- 生成时间：{checklist.meta.updated_at.isoformat()}",
        "",
    ]
    if not checklist.items:
        lines.extend(["当前需求评审没有需要产品决策的事项。", ""])
        return "\n".join(lines)

    for item in checklist.items:
        lines.extend(
            [
                f"## {item.item_id} · {_severity_label(item.severity)}",
                "",
                f"- 状态：{_status_label(item.status)}",
                f"- 来源定位：{item.location}",
                f"- 产品问题：{item.question}",
                "- 决策选项：",
            ]
        )
        lines.extend(f"  - {option}" for option in item.decision_options)
        lines.extend(
            [
                f"- 产品结论：{item.product_decision or '待产品填写'}",
                f"- 负责人：{item.owner or '待指定'}",
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


def _status_label(status: str) -> str:
    return {
        "pending": "待确认",
        "confirmed": "已确认",
        "rejected": "需补充规则",
    }[status]
