# DevOps Webhook API 文档

## 概述

DevOps Webhook 服务提供流水线记录管理、Push事件记录、构建日志查询等功能。

**基础URL**: `http://localhost:8080`

**通用响应格式**:
```json
{
  "status": "success" | "error",
  "message": "描述信息",
  "data": {},
  "count": 0
}
```

---

## 目录

1. [Webhook接口](#1-webhook接口)
2. [流水线记录接口](#2-流水线记录接口)
3. [Push记录接口](#3-push记录接口)
4. [CD记录接口](#4-cd记录接口)
5. [构建日志接口](#5-构建日志接口)

---

## 1. Webhook接口

### 1.1 Vendor Bot V2 (Subpath)

接收 `vendor_bot/v2/<subpath>` 格式的Webhook请求。

**请求**
```http
POST /vendor_bot/v2/<path:subpath>
Content-Type: application/json
```

**路径参数**
| 参数 | 类型 | 描述 |
|------|------|------|
| subpath | string | 子路径，如 `group/project` |

**响应**
```json
{
  "status": "success",
  "message": "处理成功"
}
```

---

### 1.2 Vendor Bot V2

接收 `vendor_bot/v2` 格式的Webhook请求。

**请求**
```http
POST /vendor_bot/v2
Content-Type: application/json
```

**响应**
```json
{
  "status": "success",
  "message": "处理成功"
}
```

---

### 1.3 Vendor Bot V1

接收 `vendor_bot` 格式的Webhook请求。

**请求**
```http
POST /vendor_bot
Content-Type: application/json
```

**响应**
```json
{
  "status": "success",
  "message": "处理成功"
}
```

---

### 1.4 IT Reporter

处理来自远程服务器的IT报告请求，支持从MinIO下载文件。

**请求**
```http
POST /vendor_bot/itreporter
Content-Type: application/json
```

**请求体**
```json
{
  "report_path": "path/to/report.html",
  "minio_bucket": "reports"
}
```

| 字段 | 类型 | 必填 | 描述 |
|------|------|------|------|
| report_path | string | 是 | MinIO中的报告文件路径 |
| minio_bucket | string | 是 | MinIO存储桶名称 |

**响应**
```json
{
  "status": "success",
  "card": {
    "msg_type": "interactive",
    "card": { ... }
  }
}
```

**错误响应**
```json
{
  "status": "error",
  "message": "错误描述"
}
```

---

### 1.5 Monitor Event

接收来自AlertManager的监控事件。

**请求**
```http
POST /monitor/event
Content-Type: application/json
```

**请求体**
AlertManager格式的webhook请求体。

**响应**
```json
{
  "status": "success",
  "message": "事件已接收并记录"
}
```

---

## 2. 流水线记录接口

### 2.1 获取流水线记录

获取所有流水线记录（JSON格式）。

**请求**
```http
GET /pipelines/records
```

**响应**
```json
{
  "status": "success",
  "data": [
    {
      "path_with_namespace": "group/project",
      "namespace": "group",
      "project_name": "project",
      "pipeline_path": "path/to/pipeline",
      "pipeline_iid": 123,
      "git_url": "https://gitlab.example.com/group/project.git",
      "subpath": "group/project",
      "latest_triggered_by": "user",
      "record_time": "2024-01-01T00:00:00.000Z"
    }
  ],
  "count": 1
}
```

---

### 2.2 流水线记录HTML页面

以HTML页面形式展示流水线记录。

**请求**
```http
GET /pipelines/records/view
GET /pipelines/records/view/<namespace>
```

**路径参数**
| 参数 | 类型 | 描述 |
|------|------|------|
| namespace | string | 可选，按命名空间筛选 |

**响应**
HTML页面

---

### 2.3 获取流水线记录(JSON)

获取JSON格式的流水线记录。

**请求**
```http
GET /pipelines/records/json
```

**响应**
```json
{
  "status": "success",
  "data": [...],
  "count": 10
}
```

---

## 3. Push记录接口

### 3.1 获取最新Push记录

获取特定项目的最近10条push记录。

**请求**
```http
GET /push_records/latest/<path:git_url>
```

**路径参数**
| 参数 | 类型 | 描述 |
|------|------|------|
| git_url | string | URL编码的Git仓库地址 |

**响应**
```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "project_name": "project",
      "ref": "refs/heads/main",
      "user_name": "user",
      "git_url": "https://gitlab.example.com/group/project.git",
      "subpath": "group/project",
      "push_time": "2024-01-01 00:00:00",
      "commits": [
        {
          "id": "abc123",
          "message": "commit message",
          "timestamp": "2024-01-01T00:00:00Z"
        }
      ]
    }
  ],
  "count": 10
}
```

---

## 4. CD记录接口

### 4.1 CD记录API

获取CD记录，返回存在pipeline_iid的记录。

**请求**
```http
GET /api/cd-records
GET /api/cd-records?subpath=<subpath>
```

**查询参数**
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| subpath | string | 否 | 按子路径筛选 |

**响应**
```json
{
  "status": "success",
  "records": [
    {
      "project_name": "project",
      "ref": "main",
      "user_name": "user",
      "pipeline_iid": 123,
      "push_time": "2024-01-01T00:00:00Z",
      "pipeline_status": "success",
      "deploy_ips": ["192.168.1.1"],
      "message": "deploy message",
      "subpath": "group/project"
    }
  ],
  "count": 1
}
```

---

### 4.2 CD记录管理页面

CD记录管理HTML页面。

**请求**
```http
GET /cd-records
GET /cd-records/<path:return_url>
```

**路径参数**
| 参数 | 类型 | 描述 |
|------|------|------|
| return_url | string | 可选，返回链接（`/`替换为`__`） |

**响应**
HTML页面

---

## 5. 构建日志接口

### 5.1 获取构建日志

获取指定流水号的构建日志。

**请求**
```http
GET /api/build-logs/<path:project>/<int:pipeline_iid>
```

**路径参数**
| 参数 | 类型 | 描述 |
|------|------|------|
| project | string | 项目名称（URL编码） |
| pipeline_iid | integer | 流水线ID |

**响应**
```json
{
  "status": "success",
  "data": {
    "full_log": "完整日志内容...",
    "error_summary": "错误摘要...",
    "project_name": "project",
    "pipeline_iid": 123
  }
}
```

**错误响应** (404)
```json
{
  "status": "error",
  "message": "日志文件不存在"
}
```

---

### 5.2 下载构建日志

下载构建日志文件。

**请求**
```http
GET /api/build-logs/<path:project>/<int:pipeline_iid>/download
```

**路径参数**
| 参数 | 类型 | 描述 |
|------|------|------|
| project | string | 项目名称（URL编码） |
| pipeline_iid | integer | 流水线ID |

**响应**
文件下载（Content-Type: `text/plain`）

**错误响应** (404)
```json
{
  "status": "error",
  "message": "日志文件不存在"
}
```

---

## 错误码说明

| HTTP状态码 | 描述 |
|------------|------|
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |
