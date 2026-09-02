"""Persistent background task records for workflow execution."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..storage import ArtifactStore, ArtifactStoreError


RunTaskStatus = Literal["queued", "running", "succeeded", "failed", "needs_human"]


class RunTimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    stage: str = Field(min_length=1)
    action: str = Field(min_length=1)
    message: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)


class WorkflowRunTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^run-[A-Za-z0-9]+$")
    project_id: str = Field(min_length=1)
    module_id: str | None = None
    status: RunTaskStatus
    stage: str = Field(min_length=1)
    current_step: str = Field(min_length=1)
    message: str = Field(min_length=1)
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    attempt: int = Field(default=1, ge=1)
    error: str | None = None
    timeline: list[RunTimelineEvent] = Field(default_factory=list)
    result: dict | None = None


class WorkflowRunTaskStore:
    """Persist task snapshots next to the selected project/module context."""

    def __init__(self, workspace: str | Path) -> None:
        self.projects_root = Path(workspace).resolve() / "projects"
        self._lock = RLock()

    def _path(self, project_id: str, module_id: str | None, run_id: str) -> Path:
        store = ArtifactStore(self.projects_root, module_id=module_id)
        return store.project_root(project_id) / "runs" / f"{run_id}.json"

    def save(self, task: WorkflowRunTask) -> WorkflowRunTask:
        path = self._path(task.project_id, task.module_id, task.run_id)
        ArtifactStore._write_json(path, task.model_dump(mode="json"))
        return task

    def load(self, project_id: str, run_id: str, module_id: str | None = None) -> WorkflowRunTask:
        path = self._path(project_id, module_id, run_id)
        if not path.is_file():
            raise ArtifactStoreError(f"workflow run does not exist: {run_id}")
        return WorkflowRunTask.model_validate(ArtifactStore._read_json(path))

    def latest(
        self,
        project_id: str,
        module_id: str | None = None,
    ) -> WorkflowRunTask | None:
        store = ArtifactStore(self.projects_root, module_id=module_id)
        runs_root = store.project_root(project_id) / "runs"
        paths = list(runs_root.glob("run-*.json")) if runs_root.is_dir() else []
        latest_task: WorkflowRunTask | None = None
        for path in paths:
            try:
                task = WorkflowRunTask.model_validate(ArtifactStore._read_json(path))
            except (ArtifactStoreError, ValueError):
                continue
            if latest_task is None or task.updated_at > latest_task.updated_at:
                latest_task = task
        return latest_task


TaskCallable = Callable[[WorkflowRunTaskStore, str], None]


class WorkflowTaskManager:
    """Submit workflow runs and keep at most one active run per context."""

    def __init__(self, workspace: str | Path) -> None:
        self.store = WorkflowRunTaskStore(workspace)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="butterfly-run")
        self._lock = Lock()
        self._active: dict[str, str] = {}

    @staticmethod
    def context_key(project_id: str, module_id: str | None) -> str:
        return f"{project_id}:{module_id or '__v1__'}"

    def submit(
        self,
        project_id: str,
        module_id: str | None,
        worker: TaskCallable,
    ) -> WorkflowRunTask:
        key = self.context_key(project_id, module_id)
        with self._lock:
            active_id = self._active.get(key)
            if active_id:
                active = self.store.load(project_id, active_id, module_id)
                if active.status in {"queued", "running"}:
                    return active

            now = datetime.now(timezone.utc)
            task = WorkflowRunTask(
                run_id=f"run-{uuid4().hex}",
                project_id=project_id,
                module_id=module_id,
                status="queued",
                stage="任务准备",
                current_step="等待后台执行",
                message="任务已创建，等待 Agent 执行",
                started_at=now,
                updated_at=now,
                timeline=[RunTimelineEvent(
                    occurred_at=now,
                    stage="任务准备",
                    action="任务已创建",
                    message="已进入后台执行队列",
                )],
            )
            self.store.save(task)
            self._active[key] = task.run_id
            self.executor.submit(self._run, task.run_id, project_id, module_id, worker)
            return task

    def update(
        self,
        project_id: str,
        run_id: str,
        module_id: str | None = None,
        *,
        status: RunTaskStatus | None = None,
        stage: str | None = None,
        current_step: str | None = None,
        message: str | None = None,
        attempt: int | None = None,
        error: str | None = None,
        result: dict | None = None,
        completed: bool = False,
    ) -> WorkflowRunTask:
        with self.store._lock:
            task = self.store.load(project_id, run_id, module_id)
            now = datetime.now(timezone.utc)
            task.status = status or task.status
            task.stage = stage or task.stage
            task.current_step = current_step or task.current_step
            task.message = message or task.message
            task.attempt = attempt or task.attempt
            task.error = error
            if result is not None:
                task.result = result
            task.updated_at = now
            if completed:
                task.completed_at = now
            task.timeline.append(RunTimelineEvent(
                occurred_at=now,
                stage=task.stage,
                action=task.current_step,
                message=task.message,
                attempt=task.attempt,
            ))
            return self.store.save(task)

    def _run(
        self,
        run_id: str,
        project_id: str,
        module_id: str | None,
        worker: TaskCallable,
    ) -> None:
        key = self.context_key(project_id, module_id)
        try:
            self.update(
                project_id,
                run_id,
                module_id,
                status="running",
                stage="任务准备",
                current_step="准备工作流上下文",
                message="正在读取项目、模块和当前工作流状态",
            )
            worker(self.store, run_id)
        finally:
            with self._lock:
                if self._active.get(key) == run_id:
                    self._active.pop(key, None)

