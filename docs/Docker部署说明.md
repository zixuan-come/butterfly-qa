# Butterfly Agent Docker 部署说明

## 适用范围

本配置只部署 Butterfly Agent。`WhaleTestPro` 不会被复制到镜像中，也不需要和 Butterfly Agent 放在同一个 Docker Compose 项目里。

## 前置条件

- Docker Desktop 已启动
- 当前目录为 Butterfly Agent 项目根目录

## 启动

```powershell
docker compose build
docker compose up -d
```

启动后访问：

```text
http://localhost:8000
```

查看运行状态：

```powershell
docker compose ps
docker compose logs -f butterfly-agent
```

停止服务：

```powershell
docker compose down
```

## 修改访问端口

复制 `.env.example` 为 `.env`，然后修改：

```text
BUTTERFLY_QA_PORT=8080
```

之后访问 `http://localhost:8080`。`.env` 只影响宿主机端口，不改变容器内部的 8000 端口。

## 与 WhaleTestPro 的关系

当前 V1 通过 Web 界面上传产品需求、设计资料和接口文档，Butterfly Agent 不需要读取 WhaleTestPro 的运行环境。

后续如果需要调用 WhaleTestPro 的接口，WhaleTestPro 仍应独立启动，Butterfly Agent 只通过接口地址连接。后续如果需要分析代码，再单独设计只读目录挂载或 Git 仓库接入，不把被测项目写进 Butterfly Agent 镜像。
