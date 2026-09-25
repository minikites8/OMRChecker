# 管理员管理面板

管理员登录后，侧边栏显示「管理面板」，入口为 `/#admin`。页面包含账号/存储/任务概览、用户搜索与分页、创建内置账号、角色调整、启停账号、密码重置和操作审计。

## 启用

沿用部署环境的 `DATABASE_URL`、`OMR_SESSION_SECRET`、`OMR_AUTH_MODE` 与初始管理员配置。`builtin`、`oidc_or_builtin` 模式支持创建内置账号；`oidc` 模式通过首次 OIDC 登录创建账号。重启后端后，启动流程自动为 `app_users` 添加 `session_version` 字段。

本地 `disabled` 模式沿用本地管理员身份，管理面板展示模式说明。启用登录和 PostgreSQL 后开放账号管理及审计查询。

## 接口

FastAPI 与 `scan_ui.py` 的本地 HTTP 服务共用以下接口和权限规则。请求携带 `omr_session` Cookie；写请求使用 `Content-Type: application/json`，账号写请求体上限为 16 KB，配置写请求体上限为 64 KB。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/admin/overview` | 平台模式、管理能力和数据库统计 |
| GET | `/api/admin/users` | 账号列表；`search`、`role`、`active`、`page`、`page_size` |
| POST | `/api/admin/users` | 创建内置账号；`email`、`password` 必填，`display_name`、`role` 可选 |
| PATCH | `/api/admin/users/{user_id}` | 更新 `display_name`、`role`、`is_active`、`password` 中的一项或多项 |
| GET | `/api/admin/audit-events` | 操作记录；`page`、`page_size` |

分页默认每页 20 条，上限 100 条，页码从 1 开始。角色为 `admin` / `teacher`，状态筛选值为 `true` / `false`。账号密码长度为 12–128 个字符。

成功响应包含 `ok: true`，列表附带 `items`、`total`、`page`、`page_size`。失败响应包含 `ok: false`、`error`；HTTP 状态码：401 登录要求、403 管理员权限、400 参数校验、404 账号缺失、409 账号冲突或管理员保护、413 请求大小、415 内容类型、503 管理服务状态。

```javascript
// 在管理员已登录的同源页面中创建账号。
const response = await fetch('/api/admin/users', {
  method: 'POST',
  credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    email: 'teacher@example.com',
    display_name: '数学老师',
    role: 'teacher',
    password: 'Replace-with-a-private-password'
  })
});
const result = await response.json();
```

## 权限与一致性

- 每次请求从数据库读取账号的当前角色和启用状态；角色调整即时生效。密码重置与启停状态变更递增会话版本，旧 Cookie 随即失效。
- 当前管理员保留自身角色与启用状态，系统保留至少一个有效管理员。管理员写操作在同一数据库事务中锁定账号变更、复核操作者并写入审计。
- 密码采用 Argon2 哈希。用户响应采用字段白名单，审计记录仅保存目标账号、结果状态与变更字段名。OIDC 密码由身份提供方管理。
- 任务和文件统计来自 PostgreSQL 的 `platform_jobs`、`platform_artifacts` 登记记录；文件总大小为登记文件大小之和。

## 验证

Python 测试覆盖双 HTTP 服务的角色鉴权、CRUD 参数、账号生命周期、旧会话失效、事务回滚与字段脱敏。仓储单测使用 SQLite 方言适配器，PostgreSQL 表锁语句另行断言。前端 Node 测试覆盖入口角色判断、请求字段、分页和文字渲染。

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts= -q src/tests/test_admin.py src/tests/test_platform_ui.py
node --test ui/tests/*.test.cjs
```


## 服务参数配置

「管理面板 → 服务参数配置」提供 38 个参数，分为 AI 服务、识别与资源、登录与会话、OIDC、数据存储、腾讯云 COS、服务与跨域七组。本地模式也可使用配置面板；所有配置接口沿用管理员角色校验。

- **即时生效**：AI 接口地址、模型、API Key、请求超时、单任务 AI 并发，以及默认 OCR 模式。新请求读取最新配置；每次 AI 请求从同一个配置版本读取接口、模型和密钥。任务表单显式选择的识别模式继续优先。
- **重启生效**：任务线程池、上传限制、登录方式、会话、OIDC、数据库、COS、数据目录、监听地址、端口和跨域源。页面显示待重启项；重启后的启动入口读取保存值。现有数据保留在原目录，数据库与目录切换按实际部署准备数据。
- **优先级**：管理面板保存值 → 环境变量及兼容别名 → 默认值。勾选「恢复环境 / 默认值」后保存，即移除对应覆盖。密码输入留空保留现值；填写新值或勾选「清空有效值」执行明确变更。

配置默认保存在 `data/platform-settings.json`。部署设置了 `OMR_DATA_ROOT` 时，配置保存在该启动目录的 `platform-settings.json`；可通过启动变量 `OMR_SETTINGS_FILE` 指定稳定位置。页面修改业务数据目录时，配置文件仍保留在原启动位置。Docker 默认使用 `/data/platform-settings.json`，随 `omr-data` 卷持久化。`.gitignore` 和 `.dockerignore` 已覆盖保存文件。

密码、签名密钥、数据库 URL 和云凭据以配置状态展示，响应与修改审计仅返回字段名称。服务端配置文件使用受限权限写入，部署时应保护挂载目录的访问权限。保存采用文件锁、版本校验与原子替换；同一版本并发修改返回 409，页面保留当前草稿供核对。

### 配置接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/admin/settings` | 参数定义、当前值/密钥状态、来源、配置版本、待重启项、最近配置审计 |
| PATCH | `/api/admin/settings` | 按版本保存修改或恢复环境/默认值 |

```javascript
const state = await fetch('/api/admin/settings', {
  credentials: 'include', cache: 'no-store'
}).then(response => response.json());
const result = await fetch('/api/admin/settings', {
  method: 'PATCH', credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    revision: state.revision,
    values: { HANDWRITING_AI_MODEL: 'your-model', HANDWRITING_AI_TIMEOUT: 120 },
    reset: []
  })
}).then(response => response.json());
```

`values` 只提交实际修改字段；`reset` 提交要恢复环境或默认值的参数名。同一字段在一次请求中选择一种操作。配置审计与配置在同一文件中原子保存，保留最近 200 次变更，接口返回最近 20 次。

FastAPI 推荐通过 `python -m backend.main` 启动；Dockerfile 已使用该入口，以应用面板保存的监听地址和端口。外部反向代理地址、Docker 端口映射与 PostgreSQL 服务自身账号参数由对应部署组件管理；修改服务端口时同步调整连接它的组件。
