# 飞书卡片增强功能设计方案

日期: 2026-05-14

## 概述

本方案涵盖三个飞书卡片增强功能：running 卡片加载动效、超时告警卡片跳转、失败构建 @提交人。

***

## 功能1：Running 卡片增加加载中动效（streaming\_mode）

### 背景

Pipeline running 状态的卡片缺少视觉上的"进行中"标识，用户无法直观区分正在构建和已完成。

### 方案

在 running 状态的卡片 JSON 中启用飞书 Schema 2.0 的 `streaming_mode: true`。开启后，飞书客户端会在卡片标题旁自动显示原生的「生成中」转圈动效。构建完成更新卡片时不再包含 `streaming_mode`，动效自动消失。

### 修改文件

- `src/services/message.py` — `convert_webhook_card_to_api_card` 函数
  - 当 header template 为 `wathet`（running 状态标识）时，在 config 中添加 `"streaming_mode": true`
  - 当 header template 为 `green`/`red`（success/failed 状态）时，不添加 `streaming_mode`

### 具体变更

```python
# convert_webhook_card_to_api_card 中，构建 card_content 时：
config = card.get('config', {})
if config.get('update_multi'):
    card_content["config"] = {"update_multi": True}

# 新增：running 状态启用 streaming_mode
template = header.get('template', '')
if template == 'wathet':
    if "config" not in card_content:
        card_content["config"] = {}
    card_content["config"]["streaming_mode"] = True
```

### 效果

- Running 卡片：标题旁显示飞书原生「生成中」转圈动效
- Success/Failed 卡片：无动效，正常显示

***

## 功能2：构建超时告警卡片增加 card\_link 跳转

### 背景

当前超时告警卡片没有 `card_link`，用户无法点击卡片跳转到 Pipeline 详情页查看具体情况。

### 方案

在 `send_long_build_alert` 函数中，从 `build_info` 获取 `detail_url`，添加到卡片的 `card_link` 字段。

### 修改文件

- `src/services/build_monitor.py` — `send_long_build_alert` 函数

### 具体变更

```python
# send_long_build_alert 中，构建 long_build_message 时添加 card_link：
detail_url = build_info.get('detail_url', '')

long_build_message = {
    "msg_type": "interactive",
    "card": {
        "config": {"update_multi": True},
        "card_link": {"url": detail_url},  # 新增
        "header": { ... },
        "i18n_elements": { ... }
    }
}
```

### 前置条件

- `_record_running_build` 中已存储 `detail_url`（当前代码已实现）

### 效果

- 点击超时告警卡片可跳转到对应的 Pipeline 详情页

***

## 功能3：失败构建卡片内 @提交人

### 背景

构建失败时，提交人可能未注意到群聊中的通知。通过 @提交人可以确保其收到提醒。

### 方案

在 failed 状态的卡片 markdown 中使用 `<at id="提交人用户名"/></at>` 语法。当前阶段 open\_id 接口正在开发中，先使用用户名占位；待接口就绪后替换为 `<at id="{open_id}"></at>` 实现真实 @提醒。

### 修改文件

- `src/services/message.py` — `format_message` 函数的 failed 分支

### 具体变更

```python
# format_message 中，failed 状态构建 elements 时：
if status == 'failed':
    at_user = f'<at id="{user_name}"/></at>'
    elements = [
        {
            'icon': 'member_outlined',
            'content': f"***提交人员***：{at_user}",
        },
        ...
    ]
```

### 阶段性实现

| 阶段 | 语法                        | 效果               |
| -- | ------------------------- | ---------------- |
| 当前 | `<at id="user_name"/></at>`     | 卡片内显示 @{user_name} 文本    |
| 后续 | `<at id="ou_xxxxx"></at>` | 真实 @提醒，提交人收到飞书通知 |

### 效果

- Failed 卡片中提交人员行显示为 `***提交人员***：@user_name`
- 待 open\_id 映射接口完成后升级为可点击的 @提醒

***

## 实施顺序

1. 功能2（超时告警 card\_link）— 最简单，改动最小
2. 功能1（streaming\_mode）— 中等复杂度，需注意仅 running 状态启用
3. 功能3（@提交人）— 需注意降级路径（webhook 不支持 at 语法）

## 风险与注意事项

- `streaming_mode` 仅在 Schema 2.0 中支持，webhook 降级路径无需处理（webhook 不支持此属性）
- `<at id="用户名"/>` 在 webhook 降级路径中会被当作纯文本显示，需在 `_strip_commit_from_webhook_message` 同级添加 at 语法清理逻辑
- 超时告警的 `detail_url` 依赖 `_record_running_build` 中的数据，需确认数据完整性

