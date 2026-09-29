# 多工作区 SaaS 阅卷

## 使用流程

1. 登录后进入 `/workspaces`，按学校、年级或考试创建多个阅卷工作区。
2. 点击「进入工作区」开始导入试卷、批量阅卷、复核与导出成绩。
3. 所有者在「成员与邀请」中生成邀请码和邀请链接。同事登录自己的账号后，粘贴邀请码或打开邀请链接，再点击「加入工作区」确认加入。
4. 阅卷页左侧显示当前工作区名称、成员角色与人数；点击「切换 / 管理工作区」返回工作区列表。

平台账号沿用现有内置账号与 OIDC 登录。内置账号由平台管理员创建，OIDC 账号通过现有身份提供方登录。工作区邀请授予工作区成员身份，账号认证由登录系统负责。

## 成员及邀请规则

- 每个账号可创建、加入多个工作区；创建者成为该工作区所有者。
- 工作区所有者可生成、轮换和撤销邀请。普通成员可查看工作区成员、参与阅卷。
- 默认邀请有效期 7 天、最多加入 100 位成员；接口支持有效期 1–30 天、名额 1–1000 人。
- 每次生成新邀请会撤销该工作区旧邀请；撤销邀请后，已加入的成员保留成员身份。
- 邀请码使用 12 位随机字符；链接使用随机高熵令牌。数据库仅保存 SHA-256 摘要。明文邀请码和链接在生成时展示。
- 重复加入具有幂等性；邀请剩余名额与成员写入在同一事务中完成。

## 数据隔离

请求路径显式包含工作区 ID，例如 `/w/<workspace_id>/api/review`、`/w/<workspace_id>/reviews/<review_id>/output/review.json`。服务端每次校验当前登录账号的成员身份，平台管理员访问阅卷数据时同样经过成员校验。登录模式下访问旧的无工作区数据接口会返回 HTTP 409，引导选择工作区。

每个新工作区的数据保存在：

```text
$OMR_DATA_ROOT/workspaces/<workspace_id>/
  inputs/scan_templates/
  outputs/scan_ui/
  outputs/exam_imports/
  outputs/answer_review/
  output/pdf/web_designer/
```

请求上下文通过 ContextVar 隔离，并传入扫描、批量阅卷、AI 阅卷、姓名识别等后台任务。任务索引、考生识别状态、模板管理器和浏览器中记住的批改/模板选择按工作区分组。COS 对象键使用 `<prefix>/workspaces/<workspace_id>/...`，PostgreSQL 任务键也包含工作区 ID。

## 部署

现有 FastAPI + Nginx 部署和本地 `scan_ui.py` 均支持工作区。工作区表在启动时自动创建：`app_workspaces`、`app_workspace_members`、`app_workspace_invitations`。

- 配置 `DATABASE_URL` 时，成员和邀请存储使用 PostgreSQL。
- 本地体验模式使用 `$OMR_DATA_ROOT/workspaces.sqlite3` 保存成员和邀请；`OMR_AUTH_MODE=disabled` 使用单个本地账号。
- 多账号部署使用 `OMR_AUTH_MODE=builtin`、`oidc` 或 `oidc_or_builtin`，设置 `DATABASE_URL`、`OMR_SESSION_SECRET`、`OMR_PUBLIC_BASE_URL`，并通过 HTTPS 对外提供服务。
- PostgreSQL + COS 部署沿用 `deploy/.env.example` 与 `deploy/docker-compose.yml`。多后端实例共享同一个 PostgreSQL、持久化数据卷及会话密钥，确保后台阅卷文件可见。
- Nginx 已添加 `/w/` 代理与 `/workspaces`、`/join/` 页面路由。

PowerShell 启动本地服务：

```powershell
.\run_scan_ui.ps1
```

浏览器打开 `http://localhost:8765/workspaces`。

## 原有数据迁移

检测到原有试卷、答题卡、扫描任务、批改记录或模板时，服务在首次迁移时创建 `shared`「原有阅卷工作区」。已有活跃账号成为该工作区成员，现有管理员优先成为所有者。后续新增账号通过工作区邀请加入。已有文件保留原目录，旧数据在 `/w/shared/` 中继续使用。

迁移通过数据库事务和唯一键控制，重复启动保持既有成员列表。新创建工作区从独立目录开始。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/workspaces` | 列出当前用户工作区 |
| POST | `/api/workspaces` | 创建，正文 `{"name":"高三月考"}` |
| POST | `/api/workspaces/join` | 加入，正文 `{"invite":"邀请码或邀请链接"}` |
| GET | `/api/workspaces/<id>` | 工作区与成员列表 |
| POST | `/api/workspaces/<id>/invitations` | 生成新邀请，支持 `expires_in_days`、`max_uses` |
| POST | `/api/workspaces/<id>/invitations/revoke` | 撤销当前邀请 |

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest src/tests/test_saas_workspaces.py -o addopts='' -q
node --test ui/tests/workspaces.test.cjs
```

测试覆盖多个账号和工作区、邀请码和链接、邀请过期/轮换/撤销、并发名额、重复加入、两种 HTTP 服务的越权访问、文件与试卷列表隔离、后台线程上下文和前端路径/存储隔离。
