# DevOps Webhook

DevOps Webhook 服务提供 GitLab 流水线监控、飞书卡片通知、构建日志管理、Push 事件记录等功能，支持构建失败时自动 @提交人 并提供卡片转交按钮。

## 功能特性

- **飞书卡片通知**: 构建状态实时推送至飞书群，支持 @提交人、callback 按钮转交
- **Webhook 接收**: 支持多种格式的 Webhook 接入（Vendor Bot V1/V2, IT Reporter, Monitor Event）
- **流水线记录**: 管理和展示 GitLab 流水线执行记录
- **Push 记录**: 追踪 Git push 事件和提交历史
- **CD 记录**: 持续交付记录管理
- **构建日志**: 构建日志存储、解析和下载
- **监控告警**: 接收和处理 AlertManager 监控事件
- **触发动作**: Pipeline 成功后通过 SSH 触发远程部署脚本
- **内网免认证**: 支持 ops-manager 内网免 Token 访问

## 技术栈

- **Web 框架**: Flask
- **数据库**: SQLite (WAL 模式)
- **对象存储**: MinIO
- **容器化**: Docker / Docker Compose
- **CI/CD**: GitLab CI

## 项目结构

```
devops-webhook/
├── app.py                 # 应用入口
├── config.yaml            # 配置文件
├── requirements.txt       # Python 依赖
├── Dockerfile             # Docker 构建文件
├── docker-compose.yml     # Docker Compose 配置
├── logger/                # 日志模块
├── src/
│   ├── config/            # 配置加载 (loader.py)
│   ├── routes/            # 路由定义
│   │   ├── __init__.py    # 路由注册 + 飞书回调端点
│   │   └── webhook.py     # Webhook 处理逻辑
│   ├── services/          # 业务逻辑
│   │   ├── feishu_notify.py  # 飞书通知 (API/Webhook 发送、open_id 查询、卡片存储与转发)
│   │   ├── message.py        # 消息构建 (卡片模板、callback 按钮、Webhook 降级)
│   │   ├── build_monitor.py  # 构建监控
│   │   ├── database.py       # 数据库操作
│   │   ├── gitlab_logger.py  # GitLab 日志获取
│   │   ├── log_parser.py     # 日志解析
│   │   ├── log_storage.py    # 日志存储 (本地/MinIO)
│   │   ├── record.py         # 记录管理
│   │   ├── monitor.py        # 监控告警
│   │   ├── trigger_action.py # 触发动作 (SSH 远程执行)
│   │   └── report_parser.py  # 报告解析
│   └── utils/             # 工具函数
├── static/                # 静态资源
├── templates/             # HTML 模板
├── scripts/               # 远程部署脚本
└── docs/                  # 设计文档
```

## 快速开始

### 环境要求

- Python 3.9+
- Docker & Docker Compose (可选)

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动应用
python app.py
```

服务将在 `http://localhost:8080` 启动。

### Docker 运行

```bash
# 构建镜像
docker build -t devops-webhook .

# 启动容器
docker-compose up -d
```

服务将在 `http://localhost:8032` 访问。

## 配置说明

配置文件位于 `config.yaml`，主要配置项：

### Webhook 配置

```yaml
webhook_config:
  vendor_bot: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"  # 飞书机器人 Webhook URL
default_target_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"  # 默认 Webhook URL
```

### GitLab 配置

```yaml
gitlab_config:
  gitlab_url: "http://192.168.23.19/"
  private_token: "xxx"  # 需有 read_api 权限
```

### 飞书通知配置

```yaml
notify_config:
  api_base_url: "http://localhost:8000"  # ops-manager API 地址
  # api_username: "xxx"    # 内网免认证模式下无需配置
  # api_password: "xxx"    # 内网免认证模式下无需配置
  chat_id: "oc_xxx"       # 默认飞书群聊 ID
  route_chat_id_map:       # 路由 → 群聊 ID 映射
    vendor_bot: "oc_xxx"
    default_webhook: "oc_xxx"  # 未匹配路由的 fallback
```

### 内网免认证模式

当 ops-manager 部署在内网时，可通过 `AUTH_TRUSTED_NETWORKS` 配置免认证访问，无需设置 `api_username` / `api_password`。devops-webhook 会自动检测：有凭据走 Token 认证，无凭据走内网免认证。

### 触发动作配置

```yaml
trigger_actions:
  - name: "deploy"
    project_pattern: "iam"
    ref_pattern: "release-1.0.0"
    ssh_host: "192.168.111.51"
    ssh_user: "root"
    ssh_password: "xxx"
    script: "deploy.sh"
    variables:
      DEPLOY_DIR: "/opt/software/app"
```

## API 接口

### Webhook 接口

| 端点 | 描述 |
|------|------|
| `POST /vendor_bot` | Vendor Bot V1 |
| `POST /vendor_bot/v2` | Vendor Bot V2 |
| `POST /vendor_bot/v2/<path:subpath>` | Vendor Bot V2 (Subpath) |
| `POST /vendor_bot/itreporter` | IT Reporter |
| `POST /monitor/event` | Monitor Event |

### 飞书回调接口

| 端点 | 描述 |
|------|------|
| `POST /api/feishu/card-action` | 飞书卡片动作回调（按钮点击、URL 验证） |

### 流水线记录

| 端点 | 描述 |
|------|------|
| `GET /pipelines/records` | 获取流水线记录 (JSON) |
| `GET /pipelines/records/view` | 流水线记录页面 |
| `GET /pipelines/records/json` | 获取流水线记录 (JSON) |

### Push 记录

| 端点 | 描述 |
|------|------|
| `GET /push_records/latest/<git_url>` | 获取最新 Push 记录 |

### CD 记录

| 端点 | 描述 |
|------|------|
| `GET /api/cd-records` | CD 记录列表 |
| `GET /cd-records` | CD 记录管理页面 |

### 构建日志

| 端点 | 描述 |
|------|------|
| `GET /api/build-logs/<project>/<pipeline_iid>` | 获取构建日志 |
| `GET /api/build-logs/<project>/<pipeline_iid>/download` | 下载构建日志 |

## 飞书卡片通知工作流

### 构建失败通知流程

```
GitLab Pipeline 失败
    ↓
devops-webhook 接收事件 → 解析提交人信息
    ↓
查询提交人 open_id (API → 通知记录 fallback → 纯文本 fallback)
    ↓
构建飞书卡片（含 @提交人 + callback 转交按钮）
    ↓
优先 API 发送 → 成功则存储卡片（用于后续转发）
    ↓
API 不可用 → 降级 Webhook 发送（移除 @标签和按钮，保留 Commit 信息）
```

### 卡片转交流程

```
用户点击「转交运维处理」按钮
    ↓
飞书回调 → POST /api/feishu/card-action
    ↓
handle_card_action_callback() 解析 action value
    ↓
forward_card_to_assignee() 从缓存取出卡片内容
    ↓
send_card_via_api(card, notify_user=assignee_open_id)
    ↓
卡片转发至指定负责人私聊
```

### Webhook 降级规则

当 API 不可用降级为 Webhook 发送时：
- ✅ 保留 Commit 提交信息
- ❌ 移除 `<at>` 标签（Webhook 不支持 @提及）
- ❌ 移除 callback 按钮（Webhook 不支持回调）

## CI/CD 流程

项目使用 GitLab CI 进行持续集成和部署：

- **develop-\*** 分支: 开发环境部署
- **test** 分支: 测试环境部署
- **release-\*** 分支: 预发布环境部署
- **master** 分支: 生产环境部署

详见 [.gitlab-ci.yml](.gitlab-ci.yml)。

## 许可证

MIT License
