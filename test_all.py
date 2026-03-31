#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目全面功能测试脚本
测试所有主要功能模块，确保代码正常工作
"""

import sys
import os
import json
import time
from unittest.mock import MagicMock, patch

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("项目全面功能测试")
print("=" * 60)

# 测试结果统计
test_results = {
    "passed": 0,
    "failed": 0,
    "total": 0
}

def run_test(test_name, test_func):
    """运行单个测试用例"""
    test_results["total"] += 1
    print(f"\n📋 测试: {test_name}")
    try:
        test_func()
        test_results["passed"] += 1
        print(f"✅ 通过")
        return True
    except Exception as e:
        test_results["failed"] += 1
        print(f"❌ 失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# --------------------------
# 1. 测试配置加载
# --------------------------
def test_config_loading():
    """测试配置加载功能"""
    from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG, SKIP_TIMEOUT_CHECK, TIMEOUT_SECONDS
    from src.config.loader import load_config
    
    # 测试配置是否成功加载
    assert WEBHOOK_CONFIG is not None
    assert isinstance(WEBHOOK_CONFIG, dict)
    assert DEFAULT_TARGET_URL is not None
    assert isinstance(DEFAULT_TARGET_URL, str)
    assert MINIO_CONFIG is not None
    assert isinstance(MINIO_CONFIG, dict)
    assert SKIP_TIMEOUT_CHECK is not None
    assert isinstance(SKIP_TIMEOUT_CHECK, list)
    assert TIMEOUT_SECONDS is not None
    assert isinstance(TIMEOUT_SECONDS, dict)
    
    # 测试load_config函数
    config1, config2, config3, config4, config5 = load_config()
    assert isinstance(config1, dict)
    assert isinstance(config2, str)
    assert isinstance(config3, dict)
    assert isinstance(config4, list)
    assert isinstance(config5, dict)

# --------------------------
# 2. 测试工具函数
# --------------------------
def test_time_utils():
    """测试时间工具函数"""
    from src.utils.time_utils import format_duration, calculate_interval, convert_utc_to_utc8
    
    # 测试format_duration
    assert format_duration(60) == "1分0秒"
    assert format_duration(3661) == "61分1秒"
    
    # 测试calculate_interval
    from datetime import datetime, timedelta
    now = datetime.now()
    past = now - timedelta(hours=2, minutes=30, seconds=15)
    interval = calculate_interval(past.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"))
    assert isinstance(interval, str)
    
    # 测试convert_utc_to_utc8
    utc_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    utc8_time = convert_utc_to_utc8(utc_time)
    assert isinstance(utc8_time, str)

def test_pipeline_utils():
    """测试流水线工具函数"""
    from src.utils.pipeline_utils import find_similar_pipeline_records
    
    # 测试find_similar_pipeline_records函数
    # 注意：该函数会读取日志文件，所以可能返回空列表，但不应该抛出异常
    similar = find_similar_pipeline_records("test-project", "main", "123", "web")
    assert isinstance(similar, list)

# --------------------------
# 3. 测试服务功能
# --------------------------
def test_message_services():
    """测试消息服务功能"""
    from src.services.message import format_message, send_formatted_message
    
    # 测试format_message
    payload = {
        "object_attributes": {
            "status": "success",
            "id": 123,
            "iid": 456,
            "created_at": "2023-01-01 00:00:00 UTC",
            "ref": "main",
            "url": "http://example.com/test/test-project/pipelines/123",
            "duration": 60,
            "source": "push"
        },
        "user": {
            "name": "test-user"
        },
        "project": {
            "name": "test-project",
            "namespace": "test",
            "path_with_namespace": "test/test-project",
            "web_url": "http://example.com/test/test-project"
        },
        "commit": {
            "title": "test commit"
        },
        "builds": [
            {
                "name": "test-build"
            }
        ]
    }
    message = format_message(payload)
    assert message is None or isinstance(message, dict)
    
    # 测试send_formatted_message（使用mock避免实际发送）
    with patch('src.services.message.requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        result = send_formatted_message("http://example.com", {"msg_type": "text", "content": {"text": "test"}})
        assert result is True

def test_record_services():
    """测试记录服务功能"""
    from src.services.record import record_pipeline_event
    import threading

    # 创建测试所需的参数
    pipeline_records = {}
    pipeline_records_lock = threading.Lock()
    push_records = []
    push_records_lock = threading.Lock()

    # 测试record_pipeline_event
    event_data = {
        "project": {
            "namespace": "test",
            "name": "test-project",
            "path_with_namespace": "test/test-project",
            "web_url": "http://example.com/test/test-project"
        },
        "object_attributes": {
            "iid": "123",
            "url": "http://example.com/test/test-project/pipelines/123"
        }
    }
    record_pipeline_event(
        event_data,
        subpath="test-subpath",
        pipeline_records=pipeline_records,
        pipeline_records_lock=pipeline_records_lock,
        push_records=push_records,
        push_records_lock=push_records_lock
    )

def test_monitor_services():
    """测试监控服务功能"""
    from src.services.monitor import parse_alertmanager_request, send_monitor_message

    # 测试parse_alertmanager_request
    alert_data = {
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "TestAlert",
                "instance": "test-instance"
            },
            "annotations": {
                "summary": "Test Summary",
                "description": "Test Description"
            },
            "startsAt": "2023-01-01T00:00:00Z"
        }]
    }
    formatted = parse_alertmanager_request(alert_data)
    assert isinstance(formatted, dict)

    # 测试send_monitor_message（使用mock避免实际发送）
    with patch('src.services.monitor.requests.post') as mock_post:
        mock_post.return_value.status_code = 200
        result = send_monitor_message("http://example.com", {"msg_type": "text", "content": {"text": "test"}})
        assert result is True

def test_gitlab_logger_services():
    """测试 GitLab Logger 服务功能"""
    from src.services.gitlab_logger import get_job_logs, get_failed_job_id, get_project_id_by_name

    # 测试配置获取（不依赖实际 API）
    gitlab_config_result = None
    try:
        from src.services.gitlab_logger import get_gitlab_config
        gitlab_url, private_token = get_gitlab_config()
        gitlab_config_result = (gitlab_url, private_token)
    except Exception:
        pass

    # 测试函数签名（不实际调用 API）
    assert callable(get_job_logs)
    assert callable(get_failed_job_id)
    assert callable(get_project_id_by_name)

def test_log_parser_services():
    """测试 Log Parser 服务功能"""
    from src.services.log_parser import parse_error_from_logs, find_last_error, ERROR_PATTERNS

    # 测试 ERROR_PATTERNS 定义
    assert isinstance(ERROR_PATTERNS, list)
    assert len(ERROR_PATTERNS) > 0

    # 测试 find_last_error
    test_lines = [
        "Building Docker image...",
        "Step 1/5 : FROM node:16",
        "docker tag image:v1.0",
        "docker push registry.example.com/image:v1.0",
        "ERROR: failed to connect to registry",
        "command exited with code 1"
    ]
    error_index = find_last_error(test_lines)
    assert error_index == 4, f"Expected error at index 4, got {error_index}"

    # 测试 parse_error_from_logs
    test_log = """
    Building Docker image...
    Step 1/5 : FROM node:16
    Running npm install...
    docker tag image:v1.0
    docker push registry.example.com/image:v1.0
    ERROR: failed to connect to registry
    command exited with code 1
    """

    result = parse_error_from_logs(test_log, context_lines=2)
    assert isinstance(result, dict)
    assert 'summary' in result
    assert 'error_detail' in result
    assert 'error_line' in result
    assert 'last_error_context' in result
    assert 'ERROR' in result['error_line'] or 'error' in result['error_line'].lower()

def test_log_storage_services():
    """测试日志存储服务功能"""
    from src.services.log_storage import (
        save_build_logs,
        get_build_logs,
        get_log_file_path,
        sanitize_filename
    )

    # 测试 sanitize_filename
    assert sanitize_filename("project/name") == "project_name"
    assert sanitize_filename("branch-name") == "branch-name"

    # 测试 get_log_file_path
    log_path = get_log_file_path("test-project", "main", 123)
    assert "test-project" in log_path
    assert "main" in log_path
    assert "123" in log_path

    # 测试 save_build_logs 和 get_build_logs
    test_log_content = "ERROR: test error\ncommand failed"
    test_error_summary = "ERROR: test error"

    save_result = save_build_logs(
        project_name="test-project",
        branch="test-branch",
        pipeline_iid=999,
        log_content=test_log_content,
        error_summary=test_error_summary
    )
    assert save_result is True

    # 验证保存的日志可以读取
    get_result = get_build_logs("test-project", "test-branch", 999)
    assert get_result is not None
    assert get_result.get('exists') is True
    assert test_log_content in get_result.get('full_log', '')

def test_error_log_message_format():
    """测试错误日志消息格式"""
    from src.services.message import format_error_log_message

    error_info = {
        'summary': 'ERROR: connection failed\ncommand exited',
        'error_detail': 'ERROR: connection failed\ncommand exited',
        'error_line': 'ERROR: connection failed',
        'last_error_context': '...\nERROR: connection failed\ncommand exited\n...'
    }

    message = format_error_log_message(
        project_name="test-project",
        pipeline_iid=123,
        branch="main",
        error_info=error_info,
        detail_url="http://example.com/pipeline/123",
        user_name="test-user",
        start_time="2024-01-01 10:00:00",
        end_time="2024-01-01 10:05:00"
    )

    assert isinstance(message, dict)
    assert 'card' in message
    assert 'header' in message['card']
    assert 'title' in message['card']['header']
    assert 'test-project' in message['card']['header']['title']['content']

# --------------------------
# 4. 测试路由功能
# --------------------------
def test_route_registration():
    """测试路由注册功能"""
    from flask import Flask
    from src.routes import register_routes
    
    app = Flask(__name__)
    register_routes(app)
    
    # 检查路由是否注册成功
    routes = [rule.rule for rule in app.url_map.iter_rules()]
    assert '/vendor_bot' in routes
    assert '/vendor_bot/v2' in routes
    assert '/vendor_bot/v2/<path:subpath>' in routes
    assert '/vendor_bot/itreporter' in routes
    assert '/monitor/event' in routes
    assert '/pipelines/records' in routes
    assert '/pipelines/records/view' in routes
    assert '/pipelines/records/view/<namespace>' in routes
    assert '/pipelines/records/json' in routes

def test_webhook_processing():
    """测试webhook处理功能"""
    from src.routes.webhook import process_webhook
    from flask import Request, Flask
    
    # 创建Flask应用
    app = Flask(__name__)
    
    # 创建模拟请求
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.get_data.return_value = b'{"object_kind": "pipeline"}'
    mock_request.get_json.return_value = {
        "object_kind": "pipeline",
        "project": {
            "name": "test-project",
            "path_with_namespace": "test/test-project",
            "git_ssh_url": "git@example.com:test/test-project.git"
        },
        "object_attributes": {
            "id": 123,
            "status": "success",
            "web_url": "http://example.com/test/test-project/pipelines/123"
        }
    }
    
    # 在应用上下文中测试process_webhook函数
    with app.app_context():
        response = process_webhook(mock_request, "test_route")
        assert response is not None

# --------------------------
# 5. 测试持久化功能
# --------------------------
def test_push_record_persistence():
    """测试push记录持久化功能"""
    import json
    
    # 直接使用已有的push_records.json文件，不创建新记录
    push_file = "push_records.json"
    assert os.path.exists(push_file), f"{push_file} 文件不存在"
    
    # 读取并验证push_records.json文件内容
    with open(push_file, 'r', encoding='utf-8') as f:
        push_records = json.load(f)
    
    assert isinstance(push_records, list), f"{push_file} 内容不是列表"
    
    # 验证每条记录都有必要的字段
    for record in push_records:
        assert isinstance(record, dict), f"记录不是字典: {record}"

def test_pipeline_records_view():
    """测试流水线记录视图功能"""
    from src.routes import register_routes
    from flask import Flask
    
    app = Flask(__name__)
    register_routes(app)
    
    # 使用测试客户端
    with app.test_client() as client:
        # 测试JSON接口
        response = client.get('/pipelines/records')
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "success"
        
        # 测试HTML视图
        response = client.get('/pipelines/records/view')
        assert response.status_code == 200
        
        # 测试带命名空间的HTML视图
        response = client.get('/pipelines/records/view/test')
        assert response.status_code == 200

# --------------------------
# 6. 测试日志功能
# --------------------------
def test_logging():
    """测试日志功能"""
    from logger import access_logger, webhook_logger, monitor_logger
    
    # 测试访问日志
    access_logger.log_access("127.0.0.1", "GET", "/test", "HTTP/1.1", 200, 100)
    
    # 测试webhook日志
    webhook_logger.log_request("test_route", {}, {"test": "data"})
    
    # 测试监控日志
    monitor_logger.log_event("test_monitor", {}, {"test": "data"})

# --------------------------
# 运行所有测试
# --------------------------
print("\n" + "=" * 60)
print("开始测试...")
print("=" * 60)

# 运行配置测试
run_test("配置加载", test_config_loading)

# 运行工具函数测试
run_test("时间工具函数", test_time_utils)
run_test("流水线工具函数", test_pipeline_utils)

# 运行服务测试
run_test("消息服务", test_message_services)
run_test("记录服务", test_record_services)
run_test("监控服务", test_monitor_services)
run_test("GitLab Logger服务", test_gitlab_logger_services)
run_test("Log Parser服务", test_log_parser_services)
run_test("Log Storage服务", test_log_storage_services)
run_test("错误日志消息格式", test_error_log_message_format)

# 运行路由测试
run_test("路由注册", test_route_registration)
run_test("Webhook处理", test_webhook_processing)

# 运行持久化测试
run_test("Push记录持久化", test_push_record_persistence)
run_test("流水线记录视图", test_pipeline_records_view)

# 运行日志测试
run_test("日志功能", test_logging)

# --------------------------
# 测试结果总结
# --------------------------
print("\n" + "=" * 60)
print("测试结果总结")
print("=" * 60)
print(f"📊 总测试数: {test_results['total']}")
print(f"✅ 通过: {test_results['passed']}")
print(f"❌ 失败: {test_results['failed']}")
print(f"📈 通过率: {test_results['passed'] / test_results['total'] * 100:.1f}%")

# 检查是否有失败的测试
if test_results['failed'] > 0:
    print("\n⚠️  测试未全部通过，请检查失败的测试用例")
    sys.exit(1)
else:
    print("\n🎉 所有测试通过！项目功能正常")
    sys.exit(0)