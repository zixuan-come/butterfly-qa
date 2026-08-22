"""Workflow states for the test lifecycle."""

from enum import Enum


class WorkflowState(str, Enum):
    """A stable set of states persisted in each project's workflow file."""

    REQUIREMENT_RECEIVED = "requirement_received"
    REQUIREMENT_REVIEWING = "requirement_reviewing"
    WAITING_PRODUCT_REVISION = "waiting_product_revision"

    REQUIREMENT_ANALYZING = "requirement_analyzing"
    TESTCASE_DESIGNING = "testcase_designing"
    TESTCASE_REVIEWING = "testcase_reviewing"
    WAITING_CASE_REVISION = "waiting_case_revision"
    WAITING_TESTCASE_APPROVAL = "waiting_testcase_approval"

    WAITING_MANUAL_EXECUTION = "waiting_manual_execution"
    GENERATING_REPORT = "generating_report"
    WAITING_REPORT_APPROVAL = "waiting_report_approval"

    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
