# OMRChecker 前后端分离部署

## 架构

- `backend/app.py`：FastAPI API 服务，承载登录、考试导入、扫描、批改、复核和成绩接口。
- `frontend/`：前端工程预留目录；当前页面资产仍来自 `ui/`，由 Nginx 独立容器提供。
- `deploy/nginx.conf`：同源代理 API 和文件下载，前端继续使用 `/api`、`/auth`、`/reviews` 等稳定路径。
- `platform_auth.py`：外部 OIDC 与内置账号登录。
- `platform_database.py`：PostgreSQL 用户、任务、对象元数据和审计表。
- `platform_cos.py`：腾讯云 COS 文件上传与预签名下载适配器。
- `platform_persistence.py`：PostgreSQL 元数据 + COS 对象的持久化协调器。

## 启动

1. 复制环境变量模板：

```bash
cp deploy/.env.example .env
```

2. 设置 `POSTGRES_PASSWORD`、`OMR_SESSION_SECRET`、OIDC 参数和腾讯云 COS 参数。

3. 启动前后端与 PostgreSQL：

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
```

前端地址：`http://localhost:8765`。
后端健康检查：`http://localhost:8765/api/health`。

## 登录模式

- `OMR_AUTH_MODE=oidc`：外部 OIDC 登录。
- `OMR_AUTH_MODE=builtin`：PostgreSQL 内置账号登录。
- `OMR_AUTH_MODE=oidc_or_builtin`：同时开放两种登录方式。
- `OMR_AUTH_MODE=disabled`：本地开发模式，保留现有无登录行为。

内置管理员通过 `OMR_BOOTSTRAP_ADMIN_EMAIL` 和 `OMR_BOOTSTRAP_ADMIN_PASSWORD` 首次启动创建，密码进入 PostgreSQL 前使用 Argon2 哈希。

## 持久化

生产环境使用：

- PostgreSQL：用户、考试业务元数据、任务、对象索引、审计事件。
- 腾讯云 COS：试卷、答题卡、扫描件、裁剪图、批改报告和导出文件。
- `/data`：容器内的临时工作目录和本地兼容缓存，部署时挂载持久化卷。

## 下一步迁移

1. 将现有 `scan_ui.py` 中的业务函数拆成 `backend/services`、`backend/repositories` 和 `backend/routers`。
2. 将前端页面逐步迁移到 `frontend/src`，保持 API 路径稳定。
3. 将批量批改与 AI 判分拆成独立 Worker，使用 Redis 保存任务队列和进度。
4. 将文件下载改成 COS 预签名 URL，前端通过短期链接读取对象。
5. 为考试、试卷、答题卡、成绩和复核操作补充租户权限与审计策略。


### 管理面板配置覆盖

管理员可在「管理面板 → 服务参数配置」保存 AI、OCR、并发、身份验证、数据库、COS、监听地址和端口等 37 项服务参数。保存值优先于容器环境变量，选择恢复环境/默认值即可移除覆盖。

默认配置文件 `/data/platform-settings.json` 使用现有 `omr-data` 卷持久化；变更配置文件位置使用启动变量 `OMR_SETTINGS_FILE`，并把对应目录挂载到持久卷。该文件包含服务凭据，请为目录设置合适的访问权限与备份策略。

AI 与默认 OCR 参数对后续任务即时生效。页面标记为重启生效的参数，在 `docker compose restart backend` 后应用。Dockerfile 通过 `python -m backend.main` 读取保存的监听配置；调整后端端口时同步更新 Nginx upstream 与容器网络配置。
