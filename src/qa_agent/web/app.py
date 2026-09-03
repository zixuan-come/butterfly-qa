"""FastAPI application exposing the existing Butterfly Agent domain services."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..agent_runner import AgentRunner
from ..audit import AgentAuditStore, ResilientAgentRunner
from ..codex_runner import CodexAgentRunner
from ..confirmation_checklist import (
    ARTIFACT_TYPE as CONFIRMATION_CHECKLIST_TYPE,
    build_confirmation_checklist,
    render_confirmation_checklist,
)
from ..evidence import EvidenceError, EvidenceService
from ..human_actions import (
    HumanActionError,
    HumanApprovalService,
    ManualExecutionService,
)
from ..orchestrator import WorkflowOrchestrator
from ..project import (
    FeatureModuleManager,
    FeatureModuleRecord,
    InputCategory,
    ProjectInput,
    ProjectManager,
    ProjectRecord,
)
from ..schemas import (
    ApprovalType,
    ArtifactMeta,
    ArtifactStatus,
    ExecutionBatch,
    HumanApproval,
    ProductConfirmationChecklist,
    RequirementReview,
)
from ..storage import ArtifactStore, ArtifactStoreError
from ..test_design_rendering import render_test_design
from ..workflow.models import ArtifactPointer, WorkflowRun
from ..workflow.state_machine import WorkflowStateMachine
from ..workflow.states import WorkflowState
from .run_tasks import WorkflowRunTask, WorkflowTaskManager
from .models import (
    ActiveArtifactData,
    AgentRunSummary,
    ApiResponse,
    ApprovalData,
    CreateFeatureModuleRequest,
    CreateProjectRequest,
    DeletionData,
    EvidenceData,
    FeatureModuleDetail,
    FeatureModuleListData,
    FeatureModuleSummary,
    ExecutionData,
    HealthData,
    ProjectDetail,
    ProjectInputData,
    ProjectInputPreviewData,
    ProjectListData,
    ProjectSummary,
    RunWorkflowRequest,
    SubmitApprovalRequest,
    SubmitExecutionRequest,
    UpdateFeatureModuleRequest,
    UpdateProjectRequest,
    ValidationErrorData,
    WorkflowStatusData,
    WorkflowStepData,
)


API_PREFIX = "/api/v1"
APP_VERSION = "0.1.0"
DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TEXT_PREVIEW_BYTES = 1024 * 1024
RunnerFactory = Callable[[Path, ArtifactStore], AgentRunner]

_HUMAN_STATES = {
    WorkflowState.WAITING_PRODUCT_REVISION,
    WorkflowState.WAITING_CASE_REVISION,
    WorkflowState.WAITING_TESTCASE_APPROVAL,
    WorkflowState.WAITING_MANUAL_EXECUTION,
    WorkflowState.WAITING_REPORT_APPROVAL,
    WorkflowState.MANUAL_INTERVENTION_REQUIRED,
}

_APPROVAL_TARGET_TYPES = {
    ApprovalType.RISK_ACCEPTANCE: "requirement_review",
    ApprovalType.TESTCASE_APPROVAL: "test_design",
    ApprovalType.REPORT_APPROVAL: "test_report",
}


class ApiError(Exception):
    """Expected business error that can be safely returned to an API client."""

    def __init__(
        self,
        *,
        http_status: int,
        code: str,
        message: str,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.data = data


def create_app(
    workspace: str | Path | None = None,
    *,
    runner_factory: RunnerFactory | None = None,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    max_text_preview_bytes: int = DEFAULT_MAX_TEXT_PREVIEW_BYTES,
) -> FastAPI:
    if max_upload_bytes < 1:
        raise ValueError("max_upload_bytes must be positive")
    if max_text_preview_bytes < 1:
        raise ValueError("max_text_preview_bytes must be positive")
    resolved_workspace = Path(
        workspace or os.environ.get("BUTTERFLY_QA_WORKSPACE", ".")
    ).resolve()
    app = FastAPI(
        title="Butterfly Agent API",
        version=APP_VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.workspace = resolved_workspace
    app.state.runner_factory = runner_factory or _default_runner_factory
    app.state.max_upload_bytes = max_upload_bytes
    app.state.max_text_preview_bytes = max_text_preview_bytes
    app.state.project_locks = {}
    app.state.workflow_tasks = WorkflowTaskManager(resolved_workspace)
    static_root = Path(__file__).parent / "static"
    app.state.static_root = static_root
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _json_response(
            status_code=exc.http_status,
            code=exc.code,
            message=exc.message,
            data=exc.data,
            request_id=_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = ValidationErrorData(errors=jsonable_encoder(exc.errors()))
        return _json_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="INVALID_REQUEST",
            message="请求参数校验失败",
            data=details.model_dump(mode="json"),
            request_id=_request_id(request),
        )

    @app.exception_handler(ArtifactStoreError)
    async def handle_storage_error(
        request: Request,
        exc: ArtifactStoreError,
    ) -> JSONResponse:
        return _json_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="STORAGE_OPERATION_FAILED",
            message=str(exc),
            data=None,
            request_id=_request_id(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _json_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="服务内部错误",
            data=None,
            request_id=_request_id(request),
        )

    @app.get(
        f"{API_PREFIX}/health",
        response_model=ApiResponse[HealthData],
        tags=["system"],
    )
    async def health(request: Request) -> ApiResponse[HealthData]:
        return _success(
            request,
            HealthData(
                status="healthy",
                service="butterfly-qa-api",
                version=APP_VERSION,
            ),
        )

    @app.post(
        f"{API_PREFIX}/projects",
        response_model=ApiResponse[ProjectDetail],
        status_code=status.HTTP_201_CREATED,
        tags=["projects"],
    )
    async def create_project(
        payload: CreateProjectRequest,
        request: Request,
    ) -> ApiResponse[ProjectDetail]:
        manager = _project_manager(request)
        try:
            project, workflow = manager.create_project(
                payload.project_id,
                payload.name,
                created_by=payload.created_by,
            )
        except ArtifactStoreError as exc:
            if "already exists" in str(exc):
                raise ApiError(
                    http_status=status.HTTP_409_CONFLICT,
                    code="PROJECT_ALREADY_EXISTS",
                    message="项目已存在",
                ) from exc
            raise
        return _success(
            request,
            _project_detail(project, workflow),
            message="项目创建成功",
        )

    @app.get(
        f"{API_PREFIX}/projects",
        response_model=ApiResponse[ProjectListData],
        tags=["projects"],
    )
    async def list_projects(request: Request) -> ApiResponse[ProjectListData]:
        manager = _project_manager(request)
        items: list[ProjectSummary] = []
        projects_root = manager.store.projects_root
        if projects_root.is_dir():
            for manifest_path in projects_root.glob("*/project.json"):
                project_id = manifest_path.parent.name
                try:
                    project = manager.load_project(project_id)
                    workflow = manager.load_workflow(project_id)
                except (ArtifactStoreError, ValueError):
                    continue
                items.append(_project_summary(project, workflow))
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return _success(
            request,
            ProjectListData(items=items, total=len(items)),
        )

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}",
        response_model=ApiResponse[ProjectDetail],
        tags=["projects"],
    )
    async def get_project(
        project_id: str,
        request: Request,
    ) -> ApiResponse[ProjectDetail]:
        manager = _project_manager(request)
        try:
            project = manager.load_project(project_id)
            workflow = manager.load_workflow(project_id)
        except ArtifactStoreError as exc:
            if "does not exist" in str(exc):
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="PROJECT_NOT_FOUND",
                    message="项目不存在",
                ) from exc
            raise
        return _success(request, _project_detail(project, workflow))

    @app.put(
        f"{API_PREFIX}/projects/{{project_id}}",
        response_model=ApiResponse[ProjectDetail],
        tags=["projects"],
    )
    def update_project(
        project_id: str,
        payload: UpdateProjectRequest,
        request: Request,
    ) -> ApiResponse[ProjectDetail]:
        manager = _project_manager(request)
        with _project_lock(request, project_id):
            try:
                project = manager.update_project(project_id, name=payload.name)
                workflow = manager.load_workflow(project_id)
            except ArtifactStoreError as exc:
                if "does not exist" in str(exc):
                    raise ApiError(
                        http_status=status.HTTP_404_NOT_FOUND,
                        code="PROJECT_NOT_FOUND",
                        message="项目不存在",
                    ) from exc
                raise
        return _success(request, _project_detail(project, workflow), message="项目修改成功")

    @app.delete(
        f"{API_PREFIX}/projects/{{project_id}}",
        response_model=ApiResponse[DeletionData],
        tags=["projects"],
    )
    def delete_project(
        project_id: str,
        request: Request,
    ) -> ApiResponse[DeletionData]:
        manager = _project_manager(request)
        with _project_lock(request, project_id):
            try:
                manager.delete_project(project_id)
            except ArtifactStoreError as exc:
                if "does not exist" in str(exc):
                    raise ApiError(
                        http_status=status.HTTP_404_NOT_FOUND,
                        code="PROJECT_NOT_FOUND",
                        message="项目不存在",
                    ) from exc
                raise
        return _success(
            request,
            DeletionData(resource_type="project", resource_id=project_id),
            message="项目已删除",
        )
    @app.post(
        f"{API_PREFIX}/projects/{{project_id}}/modules",
        response_model=ApiResponse[FeatureModuleDetail],
        status_code=status.HTTP_201_CREATED,
        tags=["feature-modules"],
    )
    async def create_feature_module(
        project_id: str,
        payload: CreateFeatureModuleRequest,
        request: Request,
    ) -> ApiResponse[FeatureModuleDetail]:
        try:
            manager = FeatureModuleManager(
                request.app.state.workspace / "projects",
                project_id,
                payload.module_id,
            )
            module, workflow = manager.create_module(
                payload.name,
                created_by=payload.created_by,
            )
        except ArtifactStoreError as exc:
            message = str(exc)
            if "feature module already exists" in message:
                raise ApiError(
                    http_status=status.HTTP_409_CONFLICT,
                    code="FEATURE_MODULE_ALREADY_EXISTS",
                    message="功能模块已存在",
                ) from exc
            if "does not exist" in message:
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="PROJECT_NOT_FOUND",
                    message="项目不存在",
                ) from exc
            raise
        return _success(
            request,
            _feature_module_detail(module, workflow),
            message="功能模块创建成功",
        )

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/modules",
        response_model=ApiResponse[FeatureModuleListData],
        tags=["feature-modules"],
    )
    async def list_feature_modules(
        project_id: str,
        request: Request,
    ) -> ApiResponse[FeatureModuleListData]:
        project_manager = _project_manager(request)
        _load_project(project_manager, project_id)
        items: list[FeatureModuleSummary] = []
        modules_root = project_manager.store.project_root(project_id) / "modules"
        if modules_root.is_dir():
            for manifest_path in modules_root.glob("*/module.json"):
                try:
                    manager = FeatureModuleManager(
                        project_manager.store.projects_root,
                        project_id,
                        manifest_path.parent.name,
                    )
                    module = manager.load_module()
                    workflow = manager.load_workflow(project_id)
                except (ArtifactStoreError, ValueError):
                    continue
                items.append(_feature_module_summary(module, workflow))
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return _success(
            request,
            FeatureModuleListData(items=items, total=len(items)),
        )

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/modules/{{module_id}}",
        response_model=ApiResponse[FeatureModuleDetail],
        tags=["feature-modules"],
    )
    async def get_feature_module(
        project_id: str,
        module_id: str,
        request: Request,
    ) -> ApiResponse[FeatureModuleDetail]:
        try:
            manager = FeatureModuleManager(
                request.app.state.workspace / "projects",
                project_id,
                module_id,
            )
            module = manager.load_module()
            workflow = manager.load_workflow(project_id)
        except ArtifactStoreError as exc:
            if "does not exist" in str(exc):
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="FEATURE_MODULE_NOT_FOUND",
                    message="功能模块不存在",
                ) from exc
            raise
        return _success(request, _feature_module_detail(module, workflow))

    @app.put(
        f"{API_PREFIX}/projects/{{project_id}}/modules/{{module_id}}",
        response_model=ApiResponse[FeatureModuleDetail],
        tags=["feature-modules"],
    )
    def update_feature_module(
        project_id: str,
        module_id: str,
        payload: UpdateFeatureModuleRequest,
        request: Request,
    ) -> ApiResponse[FeatureModuleDetail]:
        try:
            manager = _feature_module_manager(request, project_id, module_id)
            with _project_lock(request, project_id, module_id):
                module = manager.update_module(name=payload.name)
                workflow = manager.load_workflow(project_id)
        except ArtifactStoreError as exc:
            if "does not exist" in str(exc):
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="FEATURE_MODULE_NOT_FOUND",
                    message="功能模块不存在",
                ) from exc
            raise
        return _success(
            request,
            _feature_module_detail(module, workflow),
            message="功能模块修改成功",
        )

    @app.delete(
        f"{API_PREFIX}/projects/{{project_id}}/modules/{{module_id}}",
        response_model=ApiResponse[DeletionData],
        tags=["feature-modules"],
    )
    def delete_feature_module(
        project_id: str,
        module_id: str,
        request: Request,
    ) -> ApiResponse[DeletionData]:
        try:
            manager = _feature_module_manager(request, project_id, module_id)
            with _project_lock(request, project_id, module_id):
                manager.delete_module()
        except ArtifactStoreError as exc:
            if "does not exist" in str(exc):
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="FEATURE_MODULE_NOT_FOUND",
                    message="功能模块不存在",
                ) from exc
            raise
        return _success(
            request,
            DeletionData(resource_type="feature_module", resource_id=module_id),
            message="功能模块已删除",
        )
    @app.post(
        f"{API_PREFIX}/projects/{{project_id}}/inputs",
        response_model=ApiResponse[ProjectInputData],
        status_code=status.HTTP_201_CREATED,
        tags=["inputs"],
    )
    async def import_project_input(
        project_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
        category: Annotated[InputCategory, Form()],
        imported_by: Annotated[str, Form(min_length=1, max_length=120)],
        input_id: Annotated[str | None, Form()] = None,
        module_id: str | None = None,
    ) -> ApiResponse[ProjectInputData]:
        manager = _context_manager(request, project_id, module_id)
        _load_project(manager, project_id)
        temporary_path = await _receive_upload(request, file)
        try:
            imported = manager.import_input(
                project_id,
                temporary_path,
                category,
                imported_by=imported_by,
                input_id=input_id,
                original_name=file.filename,
            )
        except ArtifactStoreError as exc:
            if "input_id already exists" in str(exc):
                raise ApiError(
                    http_status=status.HTTP_409_CONFLICT,
                    code="INPUT_ALREADY_EXISTS",
                    message="输入文件标识已存在",
                ) from exc
            raise
        finally:
            temporary_path.unlink(missing_ok=True)
            await file.close()
        return _success(
            request,
            ProjectInputData.model_validate(imported.model_dump(mode="json")),
            message="文件导入成功",
        )

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/inputs/{{input_id}}",
        response_model=ApiResponse[ProjectInputPreviewData],
        tags=["inputs"],
    )
    async def get_project_input_preview(
        project_id: str,
        input_id: str,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[ProjectInputPreviewData]:
        store = _artifact_store(request, module_id)
        manager = _context_manager(request, project_id, module_id)
        project, _ = _load_project(manager, project_id)
        project_input = _find_project_input(project, input_id)
        path = _project_input_path(store, project_id, project_input)
        preview_kind = _input_preview_kind(project_input.media_type)
        content = None
        truncated = False
        if preview_kind == "text":
            limit = request.app.state.max_text_preview_bytes
            with path.open("rb") as reader:
                raw = reader.read(limit + 1)
            truncated = len(raw) > limit
            content = raw[:limit].decode("utf-8-sig", errors="replace")
        return _success(
            request,
            ProjectInputPreviewData(
                input=ProjectInputData.model_validate(
                    project_input.model_dump(mode="json")
                ),
                preview_kind=preview_kind,
                content=content,
                content_url=(
                    f"{API_PREFIX}/projects/{project_id}/inputs/{input_id}/content"
                    f"{'?module_id=' + module_id if module_id else ''}"
                ),
                truncated=truncated,
            ),
        )

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/inputs/{{input_id}}/content",
        response_class=FileResponse,
        tags=["inputs"],
    )
    async def get_project_input_content(
        project_id: str,
        input_id: str,
        request: Request,
        module_id: str | None = None,
    ) -> FileResponse:
        store = _artifact_store(request, module_id)
        manager = _context_manager(request, project_id, module_id)
        project, _ = _load_project(manager, project_id)
        project_input = _find_project_input(project, input_id)
        path = _project_input_path(store, project_id, project_input)
        return FileResponse(
            path,
            media_type=project_input.media_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/workflow",
        response_model=ApiResponse[WorkflowStatusData],
        tags=["workflow"],
    )
    async def get_workflow_status(
        project_id: str,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[WorkflowStatusData]:
        manager = _context_manager(request, project_id, module_id)
        _, workflow = _load_project(manager, project_id)
        return _success(request, _workflow_status(workflow, module_id))

    @app.post(
        f"{API_PREFIX}/projects/{{project_id}}/runs",
        response_model=ApiResponse[WorkflowStepData | WorkflowRunTask],
        tags=["workflow"],
    )
    def run_workflow_step(
        project_id: str,
        payload: RunWorkflowRequest,
        request: Request,
        module_id: str | None = None,
        async_run: bool = False,
    ) -> ApiResponse[WorkflowStepData | WorkflowRunTask]:
        if async_run:
            _load_project(_context_manager(request, project_id, module_id), project_id)
            task_manager = request.app.state.workflow_tasks

            def worker(_task_store, run_id):
                try:
                    task_manager.update(
                        project_id,
                        run_id,
                        module_id,
                        status="running",
                        stage="流程执行",
                        current_step="调用主流程 Agent",
                        message="正在判断当前阶段的下一步动作",
                    )
                    response = run_workflow_step(
                        project_id, payload, request, module_id, async_run=False
                    )
                    data = response.data
                    is_human = bool(
                        data
                        and data.action
                        and data.action.get("action")
                        in {"wait_human", "manual_intervention"}
                    )
                    task_manager.update(
                        project_id,
                        run_id,
                        module_id,
                        status="needs_human" if is_human else "succeeded",
                        stage="人工决策" if is_human else "流程完成",
                        current_step="等待人工处理" if is_human else "执行完成",
                        message=(
                            "Agent 已完成分析，等待人工决策"
                            if is_human
                            else "流程步骤已完成，页面状态即将刷新"
                        ),
                        result=data.model_dump(mode="json") if data else None,
                        completed=True,
                    )
                except ApiError as exc:
                    result_data = exc.data if isinstance(exc.data, dict) else None
                    result_agent = (result_data or {}).get("agent", {})
                    agent_status = result_agent.get("status")
                    failure_reason = (
                        result_agent.get("error_message")
                        or (result_data or {}).get("error")
                        or exc.message
                    )
                    task_manager.update(
                        project_id,
                        run_id,
                        module_id,
                        status=(
                            "needs_human"
                            if agent_status == "needs_human"
                            else "failed"
                        ),
                        stage=(
                            "人工介入"
                            if agent_status == "needs_human"
                            else "流程执行"
                        ),
                        current_step=(
                            "等待人工处理"
                            if agent_status == "needs_human"
                            else "执行失败"
                        ),
                        message=failure_reason,
                        error=failure_reason,
                        result=result_data,
                        completed=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    task_manager.update(
                        project_id,
                        run_id,
                        module_id,
                        status="failed",
                        stage="流程执行",
                        current_step="执行异常",
                        message="后台执行发生未处理异常",
                        error=f"{type(exc).__name__}: {exc}",
                        completed=True,
                    )

            task = task_manager.submit(project_id, module_id, worker)
            return _success(request, task, message="流程任务已开始执行")

        workspace = request.app.state.workspace
        store = _artifact_store(request, module_id)
        with _project_lock(request, project_id, module_id):
            manager = _context_manager(request, project_id, module_id)
            _, workflow = _load_project(manager, project_id)
            runner = request.app.state.runner_factory(workspace, store)
            result = WorkflowOrchestrator(
                workflow,
                runner,
                artifact_store=store,
                project_root=store.project_root(project_id),
                model=payload.model,
            ).step(trigger="web-api")
        latest_response = result.specialist_response or result.main_response
        response_data = WorkflowStepData(
            state=workflow.current_state.value,
            action=(
                result.action.model_dump(mode="json")
                if result.action is not None
                else None
            ),
            artifact_path=_project_relative_path(
                result.artifact_path,
                store.project_root(project_id),
            ),
            markdown_path=_project_relative_path(
                result.markdown_path,
                store.project_root(project_id),
            ),
            thread_id=latest_response.thread_id,
            agent=AgentRunSummary(
                role=latest_response.role.value,
                status=latest_response.status.value,
                error_type=latest_response.error_type,
                error_message=latest_response.error_message,
            ),
            transition=(
                result.transition.model_dump(mode="json")
                if result.transition is not None
                else None
            ),
            error=result.error,
        )
        if result.error:
            raise ApiError(
                http_status=status.HTTP_502_BAD_GATEWAY,
                code="AGENT_STEP_FAILED",
                message="Agent 执行失败，工作流已保留可恢复状态",
                data=response_data.model_dump(mode="json"),
            )
        return _success(request, response_data, message="流程步骤执行完成")

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/runs/latest",
        response_model=ApiResponse[WorkflowRunTask | None],
        tags=["workflow"],
    )
    def get_latest_workflow_run(
        project_id: str,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[WorkflowRunTask | None]:
        _load_project(_context_manager(request, project_id, module_id), project_id)
        task = request.app.state.workflow_tasks.store.latest(project_id, module_id)
        return _success(request, task)

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/runs/{{run_id}}",
        response_model=ApiResponse[WorkflowRunTask],
        tags=["workflow"],
    )
    def get_workflow_run(
        project_id: str,
        run_id: str,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[WorkflowRunTask]:
        _load_project(_context_manager(request, project_id, module_id), project_id)
        try:
            task = request.app.state.workflow_tasks.store.load(
                project_id, run_id, module_id
            )
        except ArtifactStoreError as exc:
            raise ApiError(
                http_status=status.HTTP_404_NOT_FOUND,
                code="WORKFLOW_RUN_NOT_FOUND",
                message="流程运行任务不存在",
            ) from exc
        return _success(request, task)
    @app.post(
        f"{API_PREFIX}/projects/{{project_id}}/approvals",
        response_model=ApiResponse[ApprovalData],
        tags=["human-actions"],
    )
    def submit_approval(
        project_id: str,
        payload: SubmitApprovalRequest,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[ApprovalData]:
        store = _artifact_store(request, module_id)
        with _project_lock(request, project_id, module_id):
            manager = _context_manager(request, project_id, module_id)
            _, workflow = _load_project(manager, project_id)
            target_type = _APPROVAL_TARGET_TYPES.get(payload.approval_type)
            if target_type is None:
                raise ApiError(
                    http_status=status.HTTP_409_CONFLICT,
                    code="HUMAN_ACTION_REJECTED",
                    message="当前 API 不支持该审批类型",
                )
            target = workflow.active_artifacts.get(target_type)
            if target is None:
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="ACTIVE_ARTIFACT_NOT_FOUND",
                    message="当前审批目标产物不存在",
                )
            now = datetime.now(timezone.utc)
            approval_id = f"approval-{uuid4().hex}"
            approval = HumanApproval(
                meta=ArtifactMeta(
                    artifact_id=approval_id,
                    artifact_type="human_approval",
                    project_id=project_id,
                    version=1,
                    status=ArtifactStatus.COMPLETED,
                    source_artifacts=[target.artifact_id],
                    created_by=payload.decided_by,
                    created_at=now,
                    updated_at=now,
                ),
                approval_type=payload.approval_type,
                target_artifact_id=target.artifact_id,
                target_artifact_type=target.artifact_type,
                target_artifact_version=target.version,
                decision=payload.decision,
                decided_by=payload.decided_by,
                decided_at=now,
                comment=payload.comment,
            )
            try:
                transition = HumanApprovalService(workflow, store).submit(approval)
            except HumanActionError as exc:
                raise _human_action_error(exc) from exc
        return _success(
            request,
            ApprovalData(
                approval_id=approval_id,
                approval_type=approval.approval_type,
                decision=approval.decision,
                target_artifact=target.model_dump(mode="json"),
                state=workflow.current_state.value,
                transition=transition.model_dump(mode="json"),
            ),
            message="审批结果已提交",
        )

    @app.post(
        f"{API_PREFIX}/projects/{{project_id}}/executions",
        response_model=ApiResponse[ExecutionData],
        status_code=status.HTTP_201_CREATED,
        tags=["human-actions"],
    )
    def submit_execution(
        project_id: str,
        payload: SubmitExecutionRequest,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[ExecutionData]:
        store = _artifact_store(request, module_id)
        with _project_lock(request, project_id, module_id):
            manager = _context_manager(request, project_id, module_id)
            _, workflow = _load_project(manager, project_id)
            design = workflow.active_artifacts.get("test_design")
            if design is None:
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="ACTIVE_ARTIFACT_NOT_FOUND",
                    message="当前测试设计产物不存在",
                )
            now = datetime.now(timezone.utc)
            execution_id = f"execution-{uuid4().hex}"
            execution = ExecutionBatch(
                meta=ArtifactMeta(
                    artifact_id=execution_id,
                    artifact_type="test_execution",
                    project_id=project_id,
                    version=1,
                    status=ArtifactStatus.COMPLETED,
                    source_artifacts=[design.artifact_id],
                    created_by=payload.submitted_by,
                    created_at=now,
                    updated_at=now,
                ),
                test_design_id=design.artifact_id,
                test_design_version=design.version,
                records=payload.records,
            )
            try:
                path, transition = ManualExecutionService(workflow, store).submit(
                    execution
                )
            except HumanActionError as exc:
                raise _human_action_error(exc) from exc
        relative_path = _project_relative_path(path, store.project_root(project_id))
        if relative_path is None:
            raise RuntimeError("execution artifact escaped project root")
        return _success(
            request,
            ExecutionData(
                execution_id=execution_id,
                artifact_path=relative_path,
                state=workflow.current_state.value,
                transition=transition.model_dump(mode="json"),
            ),
            message="测试执行结果已提交",
        )

    @app.post(
        f"{API_PREFIX}/projects/{{project_id}}/evidence",
        response_model=ApiResponse[EvidenceData],
        status_code=status.HTTP_201_CREATED,
        tags=["human-actions"],
    )
    async def import_evidence(
        project_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
        evidence_type: Annotated[str, Form()],
        description: Annotated[str, Form(min_length=1, max_length=2000)],
        evidence_id: Annotated[str | None, Form()] = None,
        module_id: str | None = None,
    ) -> ApiResponse[EvidenceData]:
        store = _artifact_store(request, module_id)
        manager = _context_manager(request, project_id, module_id)
        _load_project(manager, project_id)
        temporary_path = await _receive_upload(request, file)
        try:
            with _project_lock(request, project_id, module_id):
                evidence = EvidenceService(store).import_file(
                    project_id,
                    temporary_path,
                    evidence_type,
                    description=description,
                    evidence_id=evidence_id,
                )
        except EvidenceError as exc:
            message = str(exc)
            if "already exists" in message:
                raise ApiError(
                    http_status=status.HTTP_409_CONFLICT,
                    code="EVIDENCE_ALREADY_EXISTS",
                    message="证据标识已存在",
                ) from exc
            raise ApiError(
                http_status=status.HTTP_400_BAD_REQUEST,
                code="EVIDENCE_REJECTED",
                message=message,
            ) from exc
        except ArtifactStoreError as exc:
            if "already exists" in str(exc):
                raise ApiError(
                    http_status=status.HTTP_409_CONFLICT,
                    code="EVIDENCE_ALREADY_EXISTS",
                    message="证据标识已存在",
                ) from exc
            raise
        finally:
            temporary_path.unlink(missing_ok=True)
            await file.close()
        return _success(
            request,
            EvidenceData.model_validate(evidence.model_dump(mode="json")),
            message="证据上传成功",
        )

    @app.post(
        f"{API_PREFIX}/projects/{{project_id}}/confirmation-checklists",
        response_model=ApiResponse[ActiveArtifactData],
        status_code=status.HTTP_201_CREATED,
        tags=["artifacts"],
    )
    def generate_confirmation_checklist(
        project_id: str,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[ActiveArtifactData]:
        store = _artifact_store(request, module_id)
        manager = _context_manager(request, project_id, module_id)

        with _project_lock(request, project_id, module_id):
            _, workflow = _load_project(manager, project_id)
            review_pointer = workflow.active_artifacts.get("requirement_review")
            if review_pointer is None:
                raise ApiError(
                    http_status=status.HTTP_404_NOT_FOUND,
                    code="REQUIREMENT_REVIEW_NOT_FOUND",
                    message="请先完成需求评审，再生成产品确认清单",
                )

            review = RequirementReview.model_validate(
                store.load_artifact(
                    project_id,
                    review_pointer.artifact_type,
                    review_pointer.artifact_id,
                    review_pointer.version,
                )
            )
            current_pointer = workflow.active_artifacts.get(
                CONFIRMATION_CHECKLIST_TYPE
            )
            artifact_id = None
            version = 1
            created_at = None
            if current_pointer is not None:
                current = ProductConfirmationChecklist.model_validate(
                    store.load_artifact(
                        project_id,
                        current_pointer.artifact_type,
                        current_pointer.artifact_id,
                        current_pointer.version,
                    )
                )
                artifact_id = current.meta.artifact_id
                version = current.meta.version + 1
                created_at = current.meta.created_at

            now = datetime.now(timezone.utc)
            checklist = build_confirmation_checklist(
                review,
                project_id=project_id,
                artifact_id=artifact_id,
                version=version,
                created_at=created_at,
                now=now,
            )
            store.save_artifact(checklist)
            markdown = render_confirmation_checklist(checklist)
            markdown_path = store.save_artifact_text(
                project_id,
                checklist.meta.artifact_type,
                checklist.meta.artifact_id,
                checklist.meta.version,
                markdown,
            )
            workflow.active_artifacts[CONFIRMATION_CHECKLIST_TYPE] = (
                ArtifactPointer(
                    artifact_id=checklist.meta.artifact_id,
                    artifact_type=checklist.meta.artifact_type,
                    version=checklist.meta.version,
                )
            )
            workflow.updated_at = now
            store.save_workflow(project_id, workflow)

        return _success(
            request,
            ActiveArtifactData(
                artifact_id=checklist.meta.artifact_id,
                artifact_type=checklist.meta.artifact_type,
                version=checklist.meta.version,
                content=checklist.model_dump(mode="json"),
                markdown=markdown,
                markdown_path=_project_relative_path(
                    markdown_path,
                    store.project_root(project_id),
                ),
            ),
            message="产品确认清单生成成功",
        )

    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/artifacts/{{artifact_type}}",
        response_model=ApiResponse[ActiveArtifactData],
        tags=["artifacts"],
    )
    def get_active_artifact(
        project_id: str,
        artifact_type: str,
        request: Request,
        module_id: str | None = None,
    ) -> ApiResponse[ActiveArtifactData]:
        store = _artifact_store(request, module_id)
        manager = _context_manager(request, project_id, module_id)
        _, workflow = _load_project(manager, project_id)
        pointer = workflow.active_artifacts.get(artifact_type)
        if pointer is None:
            raise ApiError(
                http_status=status.HTTP_404_NOT_FOUND,
                code="ACTIVE_ARTIFACT_NOT_FOUND",
                message="当前产物不存在",
            )
        content = store.load_artifact(
            project_id,
            pointer.artifact_type,
            pointer.artifact_id,
            pointer.version,
        )
        artifact_dir = (
            store.project_root(project_id)
            / "artifacts"
            / pointer.artifact_type
            / pointer.artifact_id
        )
        markdown_path = artifact_dir / f"v{pointer.version}.md"
        markdown = (
            markdown_path.read_text(encoding="utf-8")
            if markdown_path.is_file()
            else None
        )
        return _success(
            request,
            ActiveArtifactData(
                artifact_id=pointer.artifact_id,
                artifact_type=pointer.artifact_type,
                version=pointer.version,
                content=content,
                markdown=markdown,
                markdown_path=(
                    _project_relative_path(
                        markdown_path,
                        store.project_root(project_id),
                    )
                    if markdown is not None
                    else None
                ),
            ),
        )


    @app.get(
        f"{API_PREFIX}/projects/{{project_id}}/artifacts/{{artifact_type}}/download",
        response_class=FileResponse,
        tags=["artifacts"],
    )
    def download_active_artifact(
        project_id: str,
        artifact_type: str,
        request: Request,
        format: str,
        module_id: str | None = None,
    ) -> FileResponse:
        if format not in {"markdown", "json"}:
            raise ApiError(
                http_status=status.HTTP_400_BAD_REQUEST,
                code="UNSUPPORTED_ARTIFACT_FORMAT",
                message="下载格式仅支持 markdown 或 json",
            )

        store = _artifact_store(request, module_id)
        manager = _context_manager(request, project_id, module_id)
        _, workflow = _load_project(manager, project_id)
        pointer = workflow.active_artifacts.get(artifact_type)
        if pointer is None:
            raise ApiError(
                http_status=status.HTTP_404_NOT_FOUND,
                code="ACTIVE_ARTIFACT_NOT_FOUND",
                message="当前产物不存在",
            )

        artifact_dir = (
            store.project_root(project_id)
            / "artifacts"
            / pointer.artifact_type
            / pointer.artifact_id
        )
        if format == "json":
            path = artifact_dir / f"v{pointer.version}.json"
            media_type = "application/json"
            extension = "json"
        else:
            path = artifact_dir / f"v{pointer.version}.md"
            if not path.is_file() and pointer.artifact_type == "test_design":
                content = store.load_artifact(
                    project_id,
                    pointer.artifact_type,
                    pointer.artifact_id,
                    pointer.version,
                )
                path = store.save_artifact_text(
                    project_id,
                    pointer.artifact_type,
                    pointer.artifact_id,
                    pointer.version,
                    render_test_design(content),
                )
            media_type = "text/markdown; charset=utf-8"
            extension = "md"

        if not path.is_file():
            raise ApiError(
                http_status=status.HTTP_404_NOT_FOUND,
                code="ARTIFACT_RENDERING_NOT_FOUND",
                message="当前产物没有可下载的 Markdown 文件",
            )

        return FileResponse(
            path,
            media_type=media_type,
            filename=f"测试用例-{pointer.artifact_id}-v{pointer.version}.{extension}",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    if (static_root / "index.html").is_file():
        app.mount(
            "/assets",
            StaticFiles(directory=static_root / "assets"),
            name="web-assets",
        )

        @app.get("/", include_in_schema=False, response_class=FileResponse)
        def web_application() -> FileResponse:
            return FileResponse(static_root / "index.html")

    return app


def _project_manager(request: Request) -> ProjectManager:
    return ProjectManager(request.app.state.workspace / "projects")


def _context_manager(
    request: Request,
    project_id: str,
    module_id: str | None,
) -> ProjectManager | FeatureModuleManager:
    if module_id is None:
        return _project_manager(request)
    return _feature_module_manager(request, project_id, module_id)


def _feature_module_manager(
    request: Request,
    project_id: str,
    module_id: str,
) -> FeatureModuleManager:
    return FeatureModuleManager(
        request.app.state.workspace / "projects",
        project_id,
        module_id,
    )


def _artifact_store(
    request: Request,
    module_id: str | None = None,
) -> ArtifactStore:
    return ArtifactStore(
        request.app.state.workspace / "projects",
        module_id=module_id,
    )


def _find_project_input(
    project: ProjectRecord | FeatureModuleRecord,
    input_id: str,
) -> ProjectInput:
    project_input = next(
        (item for item in project.inputs if item.input_id == input_id),
        None,
    )
    if project_input is None:
        raise ApiError(
            http_status=status.HTTP_404_NOT_FOUND,
            code="INPUT_NOT_FOUND",
            message="输入文件不存在",
        )
    return project_input


def _project_input_path(
    store: ArtifactStore,
    project_id: str,
    project_input: ProjectInput,
) -> Path:
    project_root = store.project_root(project_id).resolve()
    path = (project_root / project_input.relative_path).resolve()
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ApiError(
            http_status=status.HTTP_400_BAD_REQUEST,
            code="INPUT_PATH_INVALID",
            message="输入文件路径无效",
        ) from exc
    if not path.is_file():
        raise ApiError(
            http_status=status.HTTP_404_NOT_FOUND,
            code="INPUT_CONTENT_NOT_FOUND",
            message="输入文件内容不存在",
        )
    return path


def _input_preview_kind(media_type: str) -> str:
    if (
        media_type.startswith("text/")
        or media_type
        in {
            "application/json",
            "application/yaml",
            "application/xml",
        }
    ):
        return "text"
    if media_type.startswith("image/"):
        return "image"
    if media_type == "application/pdf":
        return "pdf"
    return "unsupported"


def _load_project(
    manager: ProjectManager | FeatureModuleManager,
    project_id: str,
) -> tuple[ProjectRecord | FeatureModuleRecord, WorkflowRun]:
    try:
        return manager.load_project(project_id), manager.load_workflow(project_id)
    except ArtifactStoreError as exc:
        if "does not exist" in str(exc):
            is_module = isinstance(manager, FeatureModuleManager)
            raise ApiError(
                http_status=status.HTTP_404_NOT_FOUND,
                code=(
                    "FEATURE_MODULE_NOT_FOUND"
                    if is_module
                    else "PROJECT_NOT_FOUND"
                ),
                message="功能模块不存在" if is_module else "项目不存在",
            ) from exc
        raise


async def _receive_upload(request: Request, upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "upload.bin").suffix[:20]
    temporary_dir = request.app.state.workspace / ".butterfly-qa" / "uploads"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = temporary_dir / f"{uuid4().hex}{suffix}"
    total = 0
    try:
        with temporary_path.open("xb") as writer:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > request.app.state.max_upload_bytes:
                    raise ApiError(
                        http_status=status.HTTP_413_CONTENT_TOO_LARGE,
                        code="FILE_TOO_LARGE",
                        message="上传文件超过大小限制",
                        data={"max_bytes": request.app.state.max_upload_bytes},
                    )
                writer.write(chunk)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        await upload.close()
        raise
    return temporary_path


def _workflow_status(
    workflow: WorkflowRun,
    module_id: str | None = None,
) -> WorkflowStatusData:
    available_states = WorkflowStateMachine(workflow).available_states()
    return WorkflowStatusData(
        project_id=workflow.project_id,
        module_id=module_id,
        workflow_id=workflow.workflow_id,
        state=workflow.current_state.value,
        awaiting_human=workflow.current_state in _HUMAN_STATES,
        available_states=sorted(state.value for state in available_states),
        input_files=[item.model_dump(mode="json") for item in workflow.input_files],
        current_requirement_input_id=workflow.current_requirement_input_id,
        active_artifacts={
            name: pointer.model_dump(mode="json")
            for name, pointer in workflow.active_artifacts.items()
        },
        transition_history=[
            transition.model_dump(mode="json")
            for transition in workflow.transition_history
        ],
        revision_rounds={
            "requirement": workflow.requirement_revision_rounds,
            "testcase": workflow.testcase_revision_rounds,
            "report": workflow.report_revision_rounds,
        },
        updated_at=workflow.updated_at,
    )


def _default_runner_factory(
    workspace: Path,
    store: ArtifactStore,
) -> AgentRunner:
    return ResilientAgentRunner(
        CodexAgentRunner(workspace),
        AgentAuditStore(store),
    )


def _project_lock(
    request: Request,
    project_id: str,
    module_id: str | None = None,
) -> Lock:
    locks: dict[str, Lock] = request.app.state.project_locks
    context_id = f"{project_id}:{module_id or '__v1__'}"
    return locks.setdefault(context_id, Lock())


def _project_relative_path(
    path: Path | None,
    project_root: Path,
) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None


def _feature_module_summary(
    module: FeatureModuleRecord,
    workflow: WorkflowRun,
) -> FeatureModuleSummary:
    return FeatureModuleSummary(
        module_id=module.module_id,
        project_id=module.project_id,
        name=module.name,
        state=workflow.current_state.value,
        created_by=module.created_by,
        created_at=module.created_at,
        updated_at=module.updated_at,
        input_count=len(workflow.input_files),
        active_artifact_count=len(workflow.active_artifacts),
    )


def _feature_module_detail(
    module: FeatureModuleRecord,
    workflow: WorkflowRun,
) -> FeatureModuleDetail:
    summary = _feature_module_summary(module, workflow)
    return FeatureModuleDetail(
        **summary.model_dump(),
        workflow_id=workflow.workflow_id,
        active_artifacts={
            name: pointer.model_dump(mode="json")
            for name, pointer in workflow.active_artifacts.items()
        },
        revision_rounds={
            "requirement": workflow.requirement_revision_rounds,
            "testcase": workflow.testcase_revision_rounds,
            "report": workflow.report_revision_rounds,
        },
    )


def _project_summary(
    project: ProjectRecord,
    workflow: WorkflowRun,
) -> ProjectSummary:
    return ProjectSummary(
        project_id=project.project_id,
        name=project.name,
        state=workflow.current_state.value,
        created_by=project.created_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
        input_count=len(workflow.input_files),
        active_artifact_count=len(workflow.active_artifacts),
    )


def _project_detail(
    project: ProjectRecord,
    workflow: WorkflowRun,
) -> ProjectDetail:
    summary = _project_summary(project, workflow)
    return ProjectDetail(
        **summary.model_dump(),
        workflow_id=workflow.workflow_id,
        active_artifacts={
            name: pointer.model_dump(mode="json")
            for name, pointer in workflow.active_artifacts.items()
        },
        revision_rounds={
            "requirement": workflow.requirement_revision_rounds,
            "testcase": workflow.testcase_revision_rounds,
            "report": workflow.report_revision_rounds,
        },
    )


def _success(
    request: Request,
    data: Any,
    *,
    message: str = "请求成功",
) -> ApiResponse[Any]:
    return ApiResponse(
        code="OK",
        message=message,
        data=data,
        request_id=_request_id(request),
    )


def _json_response(
    *,
    status_code: int,
    code: str,
    message: str,
    data: Any,
    request_id: str,
) -> JSONResponse:
    payload = ApiResponse[Any](
        code=code,
        message=message,
        data=data,
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload.model_dump(mode="json")),
        headers={"X-Request-ID": request_id},
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid4().hex)


def _human_action_error(exc: HumanActionError) -> ApiError:
    return ApiError(
        http_status=status.HTTP_409_CONFLICT,
        code="HUMAN_ACTION_REJECTED",
        message=str(exc),
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "qa_agent.web.app:app",
        host=os.environ.get("BUTTERFLY_QA_HOST", "127.0.0.1"),
        port=int(os.environ.get("BUTTERFLY_QA_PORT", "8000")),
        reload=False,
    )


app = create_app()
