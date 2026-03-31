# 构建失败日志获取与展示设计方案

**日期**: 2026-03-20
**版本**: v1.0
**状态**: 已批准

---

## 1. 概述

本文档描述了如何在 DevOps Webhook 系统中实现构建失败日志的自动获取、解析、通知和前端展示功能。

### 1.1 背景

当前系统已实现 GitLab CI/CD Webhook 接收和飞书消息通知。当构建失败时，需要：
- 自动获取 GitLab API 中的构建日志
- 解析日志提取错误信息
- 发送详细的错误日志通知到飞书
- 保存日志到本地便于后续分析
- 在前端页面展示失败构建的详细日志

### 1.2 目标

1. 构建失败时，通过 GitLab API 自动获取最后 100 行日志
2. 智能解析日志，提取最后一个错误命令的上下文（前5行）
3. 发送两条消息：
   - 消息1（正常构建状态）→ 路由对应的 target_url
   - 消息2（错误日志详情）→ default_target_url
4. 完整日志保存到本地文件
5. 前端 CD 记录页面支持查看失败构建的日志详情

---

## 2. 需求分析

### 2.1 功能需求

| 需求编号 | 描述 | 优先级 |
|---------|------|--------|
| F-001 | 通过 GitLab API 获取 Job Logs（最后100行） | P0 |
| F-002 | 解析日志，提取最后错误命令及上下文（前5行） | P0 |
| F-003 | 发送错误日志详情消息到 default_target_url | P0 |
| F-004 | 保存完整日志到本地文件 | P1 |
| F-005 | 前端 CD 记录页面展示失败构建日志 | P1 |

### 2.2 非功能需求

| 需求编号 | 描述 | 优先级 |
|---------|------|--------|
| N-001 | GitLab API 调用需要 Private Token 认证 | P0 |
| N-002 | 日志解析需要识别多种错误模式 | P1 |
| N-003 | 本地日志存储路径：logs/{project}/{branch}/{pipeline_iid}/ | P1 |

### 2.3 错误模式识别

日志解析需要识别以下错误模式：

```python
ERROR_PATTERNS = [
    r'error:',
    r'failed',
    r'ERROR',
    r'Failed',
    r'Exception',
    r'exception:',
    r'FAILED',
    r'\bfail\b',
    r'command.*failed',
    r'exit code \d+',
]
```

---

## 3. 架构设计

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        GitLab CI/CD                             │
│                    (Pipeline Failed)                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Webhook Receiver                             │
│                  (devops-webhook)                               │
└─────────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │   消息 1      │  │   消息 2      │  │  日志存储     │
    │ 正常构建状态   │  │ 错误日志详情   │  │  本地文件     │
    │ → route_url  │  │ → default_   │  │              │
    │              │  │   target_url  │  │              │
    └──────────────┘  └──────────────┘  └──────────────┘
                              │                 │
                              ▼                 ▼
                    ┌──────────────┐    ┌──────────────┐
                    │   飞书通知    │    │  前端日志查看  │
                    │              │    │              │
                    └──────────────┘    └──────────────┘
```

### 3.2 组件设计

| 组件 | 职责 | 文件位置 |
|------|------|----------|
| GitLab Logger | 通过 GitLab API 获取 Job Logs | src/services/gitlab_logger.py |
| Log Parser | 解析日志，提取错误信息 | src/services/log_parser.py |
| 消息构建 | 构建错误日志消息体 | src/services/message.py（扩展） |
| 前端展示 | CD 记录页面日志查看 | cd_records.html（扩展） |

---

## 4. 详细设计

### 4.1 配置项扩展

在 `config.conf` 中新增：

```json
{
  "gitlab_config": {
    "gitlab_url": "https://gitlab.company.com",
    "private_token": "your-gitlab-private-token"
  },
  "log_config": {
    "max_lines": 100,
    "error_context_lines": 5
  }
}
```

### 4.2 GitLab Logger 服务

**文件**: `src/services/gitlab_logger.py`

```python
def get_job_logs(project_id, job_id, max_lines=100):
    """
    通过 GitLab API 获取 job 日志
    Args:
        project_id: GitLab 项目 ID
        job_id: Job ID
        max_lines: 最大获取行数
    Returns:
        str: 日志内容
    """
    # 调用 GitLab API 获取日志
    # 返回最后 max_lines 行
```

```python
def get_failed_job_id(pipeline_id, project_id):
    """
    获取 pipeline 中失败的 job ID
    Args:
        pipeline_id: Pipeline ID
        project_id: GitLab 项目 ID
    Returns:
        int: 失败的 job ID
    """
    # 调用 GitLab API 获取 pipeline 详情
    # 遍历 jobs，找到 status=failed 的 job
```

### 4.3 Log Parser 服务

**文件**: `src/services/log_parser.py`

```python
ERROR_PATTERNS = [
    r'error:',
    r'failed',
    r'ERROR',
    r'Failed',
    r'Exception',
    r'exception:',
    r'FAILED',
    r'\bfail\b',
    r'command.*failed',
    r'exit code \d+',
]

def parse_error_from_logs(log_content, context_lines=5):
    """
    解析日志，提取最后错误信息
    Args:
        log_content: 原始日志内容
        context_lines: 错误上下文行数
    Returns:
        dict: {
            'summary': '最后 100 行摘要',
            'error_detail': '最后一个错误的详情（含上下文）',
            'error_line': '错误行内容',
            'last_error_context': '最后错误上下文（用于通知）'
        }
    """
    # 1. 获取最后 100 行
    # 2. 从后往前查找错误模式
    # 3. 提取错误行及上下文
    # 4. 返回结构化结果
```

### 4.4 消息构建扩展

在 `src/services/message.py` 中新增：

```python
def format_error_log_message(project_name, pipeline_iid, branch, error_info, detail_url, user_name, start_time, end_time):
    """
    构建错误日志详情消息
    Args:
        project_name: 项目名称
        pipeline_iid: Pipeline IID
        branch: 分支名称
        error_info: parse_error_from_logs 返回的错误信息
        detail_url: Pipeline 详情链接
        user_name: 构建人员
        start_time: 开始时间
        end_time: 结束时间
    Returns:
        dict: 飞书消息体
    """
```

### 4.5 Webhook 处理流程

修改 `src/routes/webhook.py` 中的处理逻辑：

```python
# 当 status == 'failed' 时：
# 1. 发送正常构建消息（已有逻辑）
# 2. 获取 GitLab Job Logs
# 3. 解析日志获取错误信息
# 4. 构建错误日志消息
# 5. 发送到 default_target_url
# 6. 保存完整日志到本地文件
```

### 4.6 日志文件存储

**存储路径**: `logs/{project_name}/{branch}/{pipeline_iid}/`

**文件结构**:
```
logs/
  └── my-project/
      └── develop/
          └── 12345/
              ├── full.log          # 完整 100 行日志
              └── error_summary.txt # 错误上下文摘要
```

---

## 5. 数据流

### 5.1 完整处理流程

```
1. Webhook 收到 GitLab pipeline failed 事件
   ↓
2. record_pipeline_event() 记录流水线事件（已有逻辑）
   ↓
3. format_message() 生成第一条正常消息
   ↓
4. 发送消息1（正常构建状态）→ route_name 对应的 target_url（已有逻辑）
   ↓
5. 判断 status == 'failed'
   ↓
6. 调用 get_gitlab_job_logs() 获取日志
   │  - 从 payload 提取 project_id, pipeline_id
   │  - 调用 get_failed_job_id() 获取失败 job_id
   │  - 调用 GitLab API 获取 job logs
   ↓
7. 调用 parse_error_from_logs() 解析错误
   │  - 提取最后 100 行
   │  - 识别错误模式
   │  - 获取错误上下文
   ↓
8. 构建错误日志消息（format_error_log_message）
   ↓
9. 发送消息2（错误日志详情）→ default_target_url
   ↓
10. 保存完整日志到本地文件
    ↓
11. 前端：用户点击失败构建记录
    → 显示日志详情模态框
    → 展示日志内容和错误摘要
```

### 5.2 消息结构

#### 消息1（正常构建状态）

已有逻辑，保持不变。

#### 消息2（错误日志详情）

发送到 `default_target_url`：

```json
{
  "msg_type": "interactive",
  "card": {
    "config": {
      "update_multi": True
    },
    "header": {
      "title": {
        "tag": "plain_text",
        "content": "🔴 构建失败日志 - {project_name}"
      },
      "subtitle": {
        "tag": "plain_text",
        "content": "Pipeline IID: {pipeline_iid} | 分支: {branch}"
      },
      "template": "red"
    },
    "i18n_elements": {
      "zh_cn": [
        {
          "tag": "markdown",
          "content": "**📋 错误摘要**\n```\n{error_summary}\n```"
        },
        {
          "tag": "markdown",
          "content": "**🔍 最后一个错误**\n```\n{last_error_context}\n```"
        },
        {
          "tag": "markdown",
          "content": "**⏱️ 时间**: {start_time} → {end_time}\n**👤 构建人员**: {user_name}"
        }
      ]
    }
  }
}
```

---

## 6. 前端设计

### 6.1 CD 记录页面扩展

在 `cd_records.html` 中：

1. **状态列点击事件**
   - 点击 `❌ 失败` 状态标签
   - 弹出日志详情模态框

2. **日志详情模态框**

```html
<div id="logDetailModal" class="modal">
  <div class="modal-content log-detail-modal">
    <div class="modal-header">
      <h2>📄 构建日志 - {project_name}</h2>
      <button class="close" onclick="closeLogDetailModal()">&times;</button>
    </div>
    <div class="log-toolbar">
      <input type="text" id="logSearchInput" placeholder="搜索关键字...">
      <button onclick="highlightLogSearch()">搜索</button>
      <button onclick="downloadLogFile()">下载日志</button>
    </div>
    <div class="log-content">
      <pre id="logContent"></pre>
    </div>
    <div class="error-summary">
      <h3>🔍 错误摘要</h3>
      <pre id="errorSummary"></pre>
    </div>
  </div>
</div>
```

3. **API 调用**
   - GET `/api/build-logs/{project_name}/{pipeline_iid}`
   - 返回日志内容和错误摘要

### 6.2 新增 API 路由

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/build-logs/<path:project>/<int:pipeline_iid>` | 获取构建日志 |
| GET | `/api/build-logs/<path:project>/<int:pipeline_iid>/download` | 下载日志文件 |

---

## 7. 实现计划

### 7.1 任务拆分

| 任务 | 描述 | 预估时间 |
|------|------|----------|
| T-001 | 创建 src/services/gitlab_logger.py | 2h |
| T-002 | 创建 src/services/log_parser.py | 2h |
| T-003 | 扩展 src/services/message.py | 1h |
| T-004 | 修改 src/routes/webhook.py | 1h |
| T-005 | 更新 config.conf 配置 | 0.5h |
| T-006 | 扩展 cd_records.html | 2h |
| T-007 | 添加日志查看 API 路由 | 1h |
| T-008 | 测试联调 | 2h |

### 7.2 实现顺序

1. **第一阶段：核心功能**
   - T-001: GitLab Logger
   - T-002: Log Parser
   - T-003: 消息构建扩展

2. **第二阶段：集成**
   - T-004: Webhook 处理逻辑
   - T-005: 配置更新

3. **第三阶段：前端**
   - T-006: CD 记录页面扩展
   - T-007: API 路由

4. **第四阶段：测试**
   - T-008: 联调测试

---

## 8. 风险与注意事项

### 8.1 风险识别

| 风险 | 描述 | 缓解措施 |
|------|------|----------|
| R-001 | GitLab API 调用失败 | 添加重试机制，记录错误日志 |
| R-002 | 日志解析不准确 | 扩展错误模式库，支持自定义 |
| R-003 | 大日志文件占用空间 | 设置日志保留策略，定期清理 |

### 8.2 注意事项

1. **GitLab Token 安全**：Private Token 必须保密，不能暴露到前端
2. **日志大小限制**：最大获取 100 行，避免内存溢出
3. **并发处理**：多个失败构建同时到达时，需要线程安全处理

---

## 9. 附录

### 9.1 GitLab API 参考

**获取 Pipeline Jobs**:
```
GET /api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs
```

**获取 Job Trace**:
```
GET /api/v4/projects/{project_id}/jobs/{job_id}/trace
```

### 9.2 文件变更清单

| 文件 | 操作 |
|------|------|
| config.conf | 修改 - 添加 gitlab_config 和 log_config |
| src/services/gitlab_logger.py | 新增 |
| src/services/log_parser.py | 新增 |
| src/services/message.py | 修改 - 添加 format_error_log_message |
| src/routes/webhook.py | 修改 - 添加日志获取和发送逻辑 |
| src/routes/__init__.py | 修改 - 添加日志查看路由 |
| cd_records.html | 修改 - 添加日志详情模态框 |
| styles.css | 修改 - 添加日志展示样式 |

---

**文档结束**
