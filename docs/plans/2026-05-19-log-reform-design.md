# 日志体系全面改造设计文档

> 日期: 2026-05-19
> 方案: A - 轻量级增强（纯文本 + ContextFilter + contextvars + 脱敏 + 可配置级别）

## 1. 现状问题

| 类别 | 问题 | 影响 |
|------|------|------|
| 格式不统一 | 中英文冒号混用、key=value/key: value 混用、emoji 滥用 | 日志难以 grep、格式不一致 |
| 业务上下文缺失 | error 日志只有 `str(e)`，缺少 project_name/pipeline_iid/route_name | 排查困难，无法定位是哪个项目/流水线 |
| 无请求追踪 | 没有 request_id，无法跨模块追踪一次请求的完整链路 | 多条日志无法关联 |
| 敏感信息泄露 | SSH 命令含密码、open_id 明文记录 | 安全风险 |
| Logger 获取混乱 | 4 种获取方式混用（模块级/函数内/import/direct logging） | 维护困难，循环导入 hack |
| 级别硬编码 | `logger.setLevel(logging.INFO)` 写死，无法动态调整 | 无法按需开启 DEBUG |
| 大对象写日志 | `f"message:{message}"` 输出完整卡片体 | 日志膨胀 |
| print 混用 | record.py 中 `print()` 绕过日志体系 | 日志丢失 |

## 2. 日志格式规范

### 2.1 格式定义

```
{时间} - {级别} - [req_id={request_id}] - {logger}:{行号} - {模块} | {操作} | {key=value上下文}
```

输出示例:
```
2026-05-19 16:34:47.554 - INFO - [req_id=a1b2c3d4] - app_logger:165 - feishu_notify | send_card | route=vendor_bot/v2/edge, project=video-storage-and-retrieval, pipeline_iid=17, method=api, success=true
```

当 request_id 为空时（非请求上下文如定时任务、启动日志）:
```
2026-05-19 16:34:47.554 - INFO - [req_id=-] - app_logger:10 - database | cleanup | deleted_push=5, deleted_pipeline=3
```

### 2.2 消息结构规范

```
{模块} | {操作} | {key=value上下文}
```

| 层级 | 说明 | 示例 |
|------|------|------|
| 模块 | 功能模块名，与文件名对应 | `feishu_notify`、`webhook`、`build_monitor`、`trigger_action` |
| 操作 | 具体操作/函数名 | `send_card`、`api_update_fallback`、`check_timeout` |
| 上下文 | key=value 对，逗号分隔 | `route=vendor_bot/v2/edge, method=api, success=true` |

### 2.3 格式规则

| 规则 | 说明 | 错误示例 | 正确示例 |
|------|------|----------|----------|
| key=value | 统一用英文 `=` 连接 | `route: vendor_bot` | `route=vendor_bot` |
| 英文标点 | 日志 key-value 用英文逗号和等号 | `提交人员：王潘` | `user=王潘` |
| 去除 emoji | 日志中不使用 emoji | `❌ 构建失败` | `构建失败` |
| 大对象截断 | 消息体/响应体最多200字符 | `message:{完整卡片}` | `card_content={"schema":"2.0"...}(truncated)` |
| 布尔值小写 | success=true/false | success=True | success=true |
| 空值显式标记 | 用 `-` 表示无值 | `deploy_ip=` | `deploy_ip=-` |

## 3. 请求链路追踪

### 3.1 contextvars 定义

新增文件 `logger/context.py`:

```python
import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('request_id', default='')
project_var: contextvars.ContextVar[str] = contextvars.ContextVar('project_name', default='')
pipeline_var: contextvars.ContextVar[str] = contextvars.ContextVar('pipeline_iid', default='')
route_var: contextvars.ContextVar[str] = contextvars.ContextVar('route_name', default='')
```

### 3.2 请求入口设置

在 `process_webhook()` 入口处:

```python
import uuid
from logger.context import request_id_var, route_var, project_var, pipeline_var

def process_webhook(request, route_name, subpath=None):
    request_id_var.set(uuid.uuid4().hex[:8])
    route_var.set(route_name)
    # ... 解析 payload 后
    project_var.set(payload.get('project', {}).get('name', ''))
    pipeline_var.set(str(payload.get('object_attributes', {}).get('iid', '')))
```

在 `_handle_failed_pipeline()` 中无需重复设置，contextvars 自动继承。

### 3.3 ContextFilter

```python
class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get('') or '-'
        record.project_name = project_var.get('') or '-'
        record.pipeline_iid = pipeline_var.get('') or '-'
        record.route_name = route_var.get('') or '-'
        return True
```

### 3.4 Formatter 更新

```python
'%(asctime)s.%(msecs)03d - %(levelname)s - [req_id=%(request_id)s] - %(name)s:%(lineno)d - %(message)s'
```

## 4. 敏感信息脱敏

新增文件 `logger/sanitize.py`:

```python
import re

_SANITIZE_PATTERNS = [
    (re.compile(r'(password\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(ssh_password\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(secret_key\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(private_token\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(open_id=)(ou_\w+)'), r'\1***'),
    (re.compile(r'(access_key\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
]

class SanitizeFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, str):
            for pattern, replacement in _SANITIZE_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True
```

## 5. 日志级别可配置

### 5.1 config.yaml 新增配置

```yaml
log_config:
  max_lines: 100
  error_context_lines: 5
  level: "INFO"
  module_levels:
    build_monitor: "DEBUG"
    feishu_notify: "INFO"
```

### 5.2 加载逻辑

在 `BaseLogger.__new__()` 中:

```python
config = get_config()
log_config = config.get('log_config', {})
global_level = getattr(logging, log_config.get('level', 'INFO').upper(), logging.INFO)
logger.setLevel(global_level)

module_levels = log_config.get('module_levels', {})
if logger.name in module_levels:
    level = getattr(logging, module_levels[logger.name].upper(), global_level)
    logger.setLevel(level)
```

## 6. Logger 获取方式统一

### 6.1 工厂函数

在 `logger/__init__.py` 中:

```python
import logging

_LOGGER_MAP = {
    'app': 'app_logger',
    'webhook': 'webhook_logger',
    'monitor': 'monitor_event_logger',
    'access': 'access_logger',
}

def get_logger(name='app'):
    return logging.getLogger(_LOGGER_MAP.get(name, name))
```

### 6.2 迁移规则

| 旧方式 | 新方式 |
|--------|--------|
| `from logger import app_logger` | `from logger import get_logger; logger = get_logger('app')` |
| `import logging; app_logger = logging.getLogger('app_logger')` | `from logger import get_logger; logger = get_logger('app')` |
| `logger = logging.getLogger('app_logger')` (模块级) | `from logger import get_logger; logger = get_logger('app')` |
| `logging.error(...)` (直接调用) | `from logger import get_logger; logger = get_logger('app'); logger.error(...)` |
| `print(f"...")` | `from logger import get_logger; logger = get_logger('app'); logger.info(...)` |

### 6.3 变量命名统一

所有模块统一使用 `logger` 变量名（不再使用 `app_logger`），避免混淆:

```python
from logger import get_logger
logger = get_logger('app')
```

## 7. 日志消息改造映射表

### 7.1 webhook.py

| 改造前 | 改造后 |
|--------|--------|
| `f"路由名称: {route_name}, 发送结果: method={result.get('method')}, success={result.get('success')}"` | `f"webhook | send_result | route={route_name}, method={result.get('method')}, success={result.get('success')}"` |
| `f"❌ format_message调用失败: {str(e)}"` | `f"webhook | format_message_failed | error={e}"` |
| `f"成功从 GitLab API 获取 Job {job_id} ({job_name}) 的日志，长度: {len(log_content)}"` | `f"webhook | fetch_job_log | source=gitlab_api, job_id={job_id}, job_name={job_name}, log_size={len(log_content)}"` |
| `f"使用 route_chat_id_map fallback 发送错误日志: {chat_id}"` | `f"webhook | error_log_fallback | chat_source=default_webhook, chat_id={chat_id}"` |
| `f"已发送错误日志消息 (日志来源: {log_source or '无'}, method={result.get('method')})"` | `f"webhook | error_log_sent | log_source={log_source or '-'}, method={result.get('method')}"` |
| `f"发送错误日志消息失败"` | `f"webhook | error_log_send_failed | project={project_name}, pipeline_iid={pipeline_iid}"` |

### 7.2 message.py

| 改造前 | 改造后 |
|--------|--------|
| `f"飞书通知通过API更新成功: route={route_name}, method=api_update"` | `f"feishu_notify | api_update_success | route={route_name}"` |
| `f"飞书通知API更新失败，降级为Webhook: route={route_name}"` | `f"feishu_notify | api_update_fallback | route={route_name}, reason=api_failed, fallback=webhook"` |
| `f"飞书通知通过API发送成功: route={route_name}, method=api"` | `f"feishu_notify | api_send_success | route={route_name}"` |
| `f"飞书通知API发送失败，降级为Webhook: route={route_name}"` | `f"feishu_notify | api_send_fallback | route={route_name}, reason=api_failed, fallback=webhook"` |
| `f"❌ 记录运行中构建失败: {str(e)}"` | `f"message | record_running_build_failed | pipeline_iid={pipeline_iid}, error={e}"` |
| `f"message:{message}"` | `f"message | format_output | project={project_name}, pipeline_iid={pipeline_iid}, status={status}"` |
| `f"当前commit_url: {commit_url}"` | `f"message | parse_payload | commit_url={commit_url}"` |

### 7.3 feishu_notify.py

| 改造前 | 改造后 |
|--------|--------|
| `f"飞书通知 API 登录失败: {str(e)}"` | `f"feishu_notify | login_failed | error={e}"` |
| `f"获取用户 open_id 成功: user={user_name}, open_id={open_id}"` | `f"feishu_notify | get_open_id | user={user_name}, open_id=***"` |
| `f"飞书卡片通知已发送: {target}, message_id={result.get('message_id')}"` | `f"feishu_notify | card_sent | target={target}, message_id={result.get('message_id')}"` |
| `f"飞书卡片通知更新失败: {result.get('error')}"` | `f"feishu_notify | card_update_failed | error={result.get('error')}"` |

### 7.4 trigger_action.py

| 改造前 | 改造后 |
|--------|--------|
| `f"触发动作 [{name}] 执行命令: {command}"` | `f"trigger_action | execute | action={name}, host={ssh_host}, command=[sanitized]"` |
| `f"触发动作 [{name}] 执行成功 (exit_code={exit_code})"` | `f"trigger_action | execute_success | action={name}, exit_code={exit_code}"` |
| `f"触发动作 [{name}] 输出:\n{output.strip()}"` | `f"trigger_action | output | action={name}, output_len={len(output.strip())}"` |

### 7.5 build_monitor.py

| 改造前 | 改造后 |
|--------|--------|
| `f"🚨 构建超时，发送告警: {pipeline_iid}"` | `f"build_monitor | timeout_alert | pipeline_iid={pipeline_iid}"` |
| `f"📊 当前运行中构建数量: {build_count}"` | `f"build_monitor | check | running_count={build_count}"` |
| `f"🔍 检查构建: {pipeline_iid}, 开始时间: {build_info['start_time']}"` | `f"build_monitor | check_build | pipeline_iid={pipeline_iid}, start_time={build_info['start_time']}"` |

## 8. 实施步骤

### Phase 1: 基础设施（不改变现有行为）

1. 新增 `logger/context.py` - contextvars 定义
2. 新增 `logger/sanitize.py` - 脱敏 Filter
3. 修改 `logger/webhook_logger.py`:
   - 添加 ContextFilter、SanitizeFilter
   - 更新 Formatter 格式字符串
   - 添加日志级别配置加载
   - 删除冗余的 `_create_access_formatter()`
4. 新增 `logger/__init__.py` 中的 `get_logger()` 工厂函数
5. 更新 `config.yaml` 添加 `log_config.level` 和 `log_config.module_levels`

### Phase 2: 请求入口改造

1. 修改 `webhook.py` 的 `process_webhook()` - 设置 contextvars
2. 修改 `routes/__init__.py` 各路由入口 - 设置 contextvars

### Phase 3: 日志消息改造（逐文件）

1. `webhook.py` - 约 20 处
2. `message.py` - 约 30 处
3. `feishu_notify.py` - 约 25 处
4. `trigger_action.py` - 约 10 处
5. `build_monitor.py` - 约 10 处
6. `database.py` - 约 10 处
7. `gitlab_logger.py` - 约 8 处
8. `log_parser.py` - 约 3 处
9. `log_storage.py` - 约 5 处
10. `record.py` - 约 6 处（含 print → logger 替换）
11. `routes/__init__.py` - 约 15 处
12. `services/__init__.py` - 约 6 处

### Phase 4: Logger 获取方式统一

1. 逐文件替换 `app_logger`/`logger` 为 `get_logger('app')`
2. 消除函数内 `import logging; app_logger = logging.getLogger('app_logger')` 模式
3. 替换 `logging.error()` 直接调用
4. 替换 `print()` 调用

### Phase 5: 验证

1. 启动服务，发送测试 webhook 请求
2. 验证 request_id 链路追踪
3. 验证敏感信息脱敏
4. 验证日志级别配置生效
5. 验证格式规范一致性

## 9. 风险与回退

| 风险 | 缓解措施 |
|------|----------|
| contextvars 在异步场景下的行为 | Flask 同步模型无此问题；若将来迁移异步需验证 |
| SanitizeFilter 正则性能 | 仅对 record.msg 做正则，单条日志开销 < 0.1ms |
| 大规模日志消息改动可能引入拼写错误 | Phase 3 逐文件改造，每文件改造后验证 |
| 现有日志分析脚本依赖旧格式 | 保留旧格式中的时间戳、级别、logger名、行号，仅改变消息部分 |
