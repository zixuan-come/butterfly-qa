import json
import socket
import threading
from datetime import datetime, timezone

from qa_agent.agent_protocol import AgentResponse, AgentRole, AgentStatus
from qa_agent.agent_runner import AgentRunner
from qa_agent.cli import _open_browser_when_ready, main


class FailedRunner(AgentRunner):
    def run(self, request):
        timestamp = datetime.now(timezone.utc)
        return AgentResponse(
            request_id=request.request_id,
            role=AgentRole.MAIN_FLOW,
            status=AgentStatus.FAILED,
            error_type="runtime_unavailable",
            error_message="Codex runtime unavailable",
            started_at=timestamp,
            completed_at=timestamp,
        )


def test_cli_creates_project_imports_input_and_shows_status(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    requirement = tmp_path / "requirement.md"
    requirement.write_text("# 修改收货地址", encoding="utf-8")

    assert main(
        [
            "--workspace",
            str(workspace),
            "project",
            "create",
            "demo",
            "--name",
            "演示项目",
            "--created-by",
            "tester-001",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "--workspace",
            str(workspace),
            "input",
            "add",
            "demo",
            str(requirement),
            "--category",
            "requirement",
            "--imported-by",
            "tester-001",
            "--input-id",
            "requirement-001",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "--workspace",
            str(workspace),
            "status",
            "demo",
            "--json",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["state"] == "requirement_received"
    assert output["input_count"] == 1


def test_cli_returns_nonzero_and_prints_error_for_missing_project(tmp_path, capsys):
    assert main(
        [
            "--workspace",
            str(tmp_path),
            "status",
            "missing",
        ]
    ) == 1

    assert "错误：" in capsys.readouterr().err


def test_cli_imports_evidence_and_writes_manifest(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "failure.log"
    source.write_text("payment timeout\n", encoding="utf-8")

    assert main(
        [
            "--workspace",
            str(workspace),
            "project",
            "create",
            "demo",
            "--name",
            "演示项目",
            "--created-by",
            "tester-001",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "--workspace",
            str(workspace),
            "evidence",
            "add",
            "demo",
            str(source),
            "--type",
            "log",
            "--description",
            "支付超时日志",
            "--evidence-id",
            "payment-timeout",
        ]
    ) == 0
    evidence = json.loads(capsys.readouterr().out)

    assert evidence["evidence_id"] == "payment-timeout"
    assert evidence["path"] == "evidence/payment-timeout.log"
    assert evidence["sha256"]
    assert (
        workspace / "projects" / "demo" / "evidence" / "payment-timeout.json"
    ).is_file()


def test_cli_run_includes_agent_failure_details(tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert main(
        [
            "--workspace",
            str(workspace),
            "project",
            "create",
            "demo",
            "--name",
            "联调失败项目",
            "--created-by",
            "tester-001",
        ]
    ) == 0
    capsys.readouterr()
    monkeypatch.setattr(
        "qa_agent.cli.CodexAgentRunner",
        lambda _workspace: FailedRunner(),
    )

    assert main(["--workspace", str(workspace), "run", "demo"]) == 1
    output = json.loads(capsys.readouterr().out)

    assert output["agent"]["role"] == "main_flow"
    assert output["agent"]["status"] == "needs_human"
    assert output["agent"]["error_type"] == "retry_exhausted"
    assert "Codex runtime unavailable" in output["agent"]["error_message"]


def test_cli_web_serves_selected_workspace_without_opening_browser(
    tmp_path,
    capsys,
    monkeypatch,
):
    captured = {}

    def fake_run(app, **options):
        captured["app"] = app
        captured["options"] = options

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(
        "qa_agent.cli.webbrowser.open",
        lambda _url: (_ for _ in ()).throw(AssertionError("browser opened")),
    )

    assert main(
        [
            "--workspace",
            str(tmp_path),
            "web",
            "--host",
            "0.0.0.0",
            "--port",
            "8123",
            "--no-open",
        ]
    ) == 0

    assert captured["app"].state.workspace == tmp_path.resolve()
    assert captured["options"] == {
        "host": "0.0.0.0",
        "port": 8123,
        "log_level": "info",
    }
    assert "http://127.0.0.1:8123/" in capsys.readouterr().out


def test_cli_web_rejects_invalid_port(tmp_path, capsys):
    assert main(
        ["--workspace", str(tmp_path), "web", "--port", "70000", "--no-open"]
    ) == 1
    assert "端口必须在 1 到 65535 之间" in capsys.readouterr().err


def test_web_browser_opens_only_after_port_is_ready(monkeypatch):
    opened = []
    ready = threading.Event()

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        monkeypatch.setattr("qa_agent.cli.webbrowser.open", opened.append)
        worker = threading.Thread(
            target=_open_browser_when_ready,
            args=(f"http://127.0.0.1:{port}/", "127.0.0.1", port),
            kwargs={"timeout_seconds": 2.0},
        )
        worker.start()
        assert opened == []
        server.listen()
        ready.set()
        worker.join(timeout=2.0)

    assert ready.is_set()
    assert opened == [f"http://127.0.0.1:{port}/"]
