# DevOps Webhook

DevOps Webhook 服务提供流水线记录管理、Push事件记录、构建日志查询等功能。

## 功能特性

- **Webhook 接收**: 支持多种格式的 Webhook 接入（Vendor Bot V1/V2, IT Reporter, Monitor Event）
- **流水线记录**: 管理和展示 GitLab 流水线执行记录
- **Push 记录**: 追踪 Git push 事件和提交历史
- **CD 记录**: 持续交付记录管理
- **构建日志**: 构建日志存储、解析和下载
- **监控告警**: 接收和处理 AlertManager 监控事件

## 技术栈

- **Web 框架**: Flask
- **数据库**: SQLite
- **对象存储**: MinIO
- **容器化**: Docker / Docker Compose
- **CI/CD**: GitLab CI

## 项目结构

```
devops-webhook/
├── app.py                 # 应用入口
├── requirements.txt       # Python 依赖
├── Dockerfile             # Docker 构建文件
├── docker-compose.yml     # Docker Compose 配置
├── migrate_to_sqlite.py   # 数据库迁移脚本
├── logger/                # 日志模块
│   ├── __init__.py
│   └── webhook_logger.py
├── src/
│   ├── config/            # 配置加载
│   ├── routes/            # 路由定义
│   ├── services/          # 业务逻辑
│   │   ├── build_monitor.py
│   │   ├── database.py
│   │   ├── gitlab_logger.py
│   │   ├── log_parser.py
│   │   ├── log_storage.py
│   │   ├── message.py
│   │   ├── monitor.py
│   │   └── record.py
│   └── utils/             # 工具函数
├── static/                # 静态资源
├── templates/              # HTML 模板
└── docs/                  # 设计文档
```

## 快速开始

### 环境要求

- Python 3.9+
- Docker & Docker Compose

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

配置文件位于 `config/config.conf`，主要配置项：

- 数据库连接
- MinIO 存储配置
- GitLab API 配置
- 日志级别

## API 接口

### Webhook 接口

| 端点 | 描述 |
|------|------|
| `POST /vendor_bot` | Vendor Bot V1 |
| `POST /vendor_bot/v2` | Vendor Bot V2 |
| `POST /vendor_bot/v2/<path:subpath>` | Vendor Bot V2 (Subpath) |
| `POST /vendor_bot/itreporter` | IT Reporter |
| `POST /monitor/event` | Monitor Event |

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

详细 API 文档请参考 [API_DOC.md](API_DOC.md)。

## CI/CD 流程

项目使用 GitLab CI 进行持续集成和部署：

- **develop-* 分支**: 开发环境部署
- **test 分支**: 测试环境部署
- **release-* 分支**: 预发布环境部署
- **master 分支**: 生产环境部署

详见 [.gitlab-ci.yml](.gitlab-ci.yml)。

## 许可证

MIT License
