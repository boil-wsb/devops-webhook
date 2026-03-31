# SQLite 数据迁移设计方案

## 1. 背景与目标

### 1.1 当前问题
- JSON 文件（`push_records.json`、`project.json`）在大数据量下读取性能差
- 文件并发写入可能导致数据丢失
- 数据一致性问题

### 1.2 迁移目标
- 提高查询性能
- 获得更好的数据一致性
- 保持 30 天数据保留策略（与日志一致）

## 2. 设计决策

### 2.1 存储策略
- **日志文件**：保持文件存储（`logs/` 目录）
- **元数据**：迁移到 SQLite（push 记录、pipeline 信息）
- **单数据库文件**：`webhook.db`，使用 WAL 模式提升并发性能

### 2.2 commits 字段处理
- 保持 JSON 字段存储，不拆分成独立表
- 便于保持原有数据结构，减少复杂度

### 2.3 数据保留策略
- 保留 30 天数据，与日志保留策略一致
- 每天凌晨执行清理任务

## 3. 数据库架构

### 3.1 ER 图

```
┌─────────────────────┐     ┌─────────────────────┐
│   push_records      │     │  pipeline_records   │
├─────────────────────┤     ├─────────────────────┤
│ id (PK)             │     │ path_with_namespace │
│ project_name        │     │       (PK)          │
│ ref                 │     │ namespace           │
│ user_name           │     │ project_name        │
│ git_url             │     │ pipeline_path       │
│ subpath             │     │ pipeline_iid        │
│ push_time           │     │ git_url             │
│ commits (JSON)      │     │ subpath            │
│ created_at          │     │ latest_triggered_by │
└─────────────────────┘     │ record_time        │
                              │ branch            │
                              │ commit_url        │
                              │ created_at        │
                              └───────────────────┘

┌─────────────────────┐
│  migration_history   │
├─────────────────────┤
│ id (PK)             │
│ source_file         │
│ records_migrated    │
│ migrated_at         │
└─────────────────────┘
```

### 3.2 表结构

#### push_records 表
```sql
CREATE TABLE push_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    ref TEXT NOT NULL,
    user_name TEXT,
    git_url TEXT,
    subpath TEXT,
    push_time TEXT,
    commits TEXT,
    created_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day'))
);

CREATE INDEX idx_push_project_time ON push_records(project_name, push_time);
CREATE INDEX idx_push_git_url ON push_records(git_url);
```

#### pipeline_records 表
```sql
CREATE TABLE pipeline_records (
    path_with_namespace TEXT PRIMARY KEY,
    namespace TEXT,
    project_name TEXT,
    pipeline_path TEXT,
    pipeline_iid INTEGER,
    git_url TEXT,
    subpath TEXT,
    latest_triggered_by TEXT,
    record_time TEXT,
    branch TEXT,
    commit_url TEXT,
    created_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day'))
);
```

#### migration_history 表
```sql
CREATE TABLE migration_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    records_migrated INTEGER,
    migrated_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day'))
);
```

## 4. 数据迁移策略

### 4.1 迁移流程

```
启动应用
    │
    ├─→ 检查 JSON 文件是否存在
    │       │
    │       ├─→ 不存在：直接使用 SQLite
    │       │
    │       └─→ 存在：执行迁移
    │               │
    │               ├─→ 读取 push_records.json
    │               ├─→ 读取 project.json
    │               ├─→ 导入到 SQLite
    │               ├─→ 记录迁移历史
    │               └─→ 保留原文件作备份
    │
    └─→ 正常运行，所有写入到 SQLite
```

### 4.2 迁移脚本主要逻辑
```python
def migrate_existing_data():
    # 1. 读取 push_records.json
    # 2. 读取 project.json
    # 3. 批量导入到 SQLite
    # 4. 记录迁移历史到 migration_history 表
    # 5. 返回迁移结果（数量、耗时）
```

## 5. API 改造

### 5.1 前端读取逻辑改动

| API 路由 | 数据来源 | 改动说明 |
|----------|----------|----------|
| `/api/cd-records` | SQLite | 支持 subpath 筛选，从 commits JSON 中提取 pipeline_iid |
| `/push_records/latest/<git_url>` | SQLite | 按 git_url 查询最近 10 条 |
| `/pipelines/records` | SQLite | 返回所有流水线记录 |
| `/pipelines/records/view` | SQLite | HTML 页面展示 |

### 5.2 核心查询示例

```sql
-- /api/cd-records 查询
SELECT id, project_name, ref, user_name, subpath,
       commits,
       push_time
FROM push_records
WHERE subpath = ? OR ? IS NULL
  AND commits LIKE '%"pipeline_iid"%'
ORDER BY push_time DESC
LIMIT 1000;
```

## 6. 30 天数据保留

### 6.1 清理任务
```sql
-- 每天凌晨 3 点执行
DELETE FROM push_records
WHERE created_at < datetime('now', '-30 days', '+8 hours', 'start of day');

DELETE FROM pipeline_records
WHERE created_at < datetime('now', '-30 days', '+8 hours', 'start of day');
```

### 6.2 清理触发
- 随应用启动时检查并清理
- 可选：独立定时任务

## 7. 改动点汇总

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/services/database.py` | 新增 | DB 初始化、CRUD 操作、清理任务 |
| `src/services/record.py` | 修改 | JSON 文件操作 → SQLite 操作 |
| `src/routes/__init__.py` | 修改 | API 读取改为从 DB 查询 |
| `src/services/message.py` | 修改 | 读取 push_records 改为从 DB 查询 |
| `migrate_to_sqlite.py` | 新增 | 一次性迁移脚本 |
| `config.conf` | 修改 | 新增 `db_config` 配置项 |

## 8. 线程安全保证

- SQLite WAL 模式允许多个读取并发
- 写入使用排他锁
- 保持原有的 `push_records_lock` 锁机制
- 连接池管理避免连接泄漏

## 9. 配置项

```json
{
  "db_config": {
    "db_path": "webhook.db",
    "wal_mode": true,
    "retention_days": 30,
    "cleanup_hour": 3
  }
}
```
