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
    from src.config import WEBHOOK_CONFIG, DEFAULT_TARGET_URL, MINIO_CONFIG
    from src.config.loader import load_config
    
    # 测试配置是否成功加载
    assert WEBHOOK_CONFIG is not None
    assert isinstance(WEBHOOK_CONFIG, dict)
    assert DEFAULT_TARGET_URL is not None
    assert isinstance(DEFAULT_TARGET_URL, str)
    assert MINIO_CONFIG is not None
    assert isinstance(MINIO_CONFIG, dict)
    
    # 测试load_config函数
    config1, config2, config3 = load_config()
    assert isinstance(config1, dict)
    assert isinstance(config2, str)
    assert isinstance(config3, dict)

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
    from src.services.record import record_pipeline_event, record_push_event
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
    record_pipeline_event(event_data, subpath="test-subpath", pipeline_records=pipeline_records, pipeline_records_lock=pipeline_records_lock)
    
    # 测试record_push_event
    push_record = {
        "project_name": "test-project",
        "ref": "refs/heads/main",
        "user_name": "test-user",
        "commits": [{
            "url": "http://example.com/test/test-project/commit/123",
            "message": "test commit",
            "timestamp": "2023-01-01T00:00:00Z"
        }]
    }
    record_push_event(push_record, push_records=push_records, push_records_lock=push_records_lock)

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
    import threading
    from src.services.record import record_push_event
    
    # 确保push_records.json文件存在
    push_file = "push_records.json"
    if os.path.exists(push_file):
        os.remove(push_file)
    
    # 创建测试所需的参数
    push_records = []
    push_records_lock = threading.Lock()
    
    # 记录一条push事件
    push_record = {
        "project_name": "test-persist",
        "ref": "refs/heads/main",
        "user_name": "test-user",
        "commits": [{
            "url": "http://example.com/test/test-persist/commit/123",
            "message": "test commit for persistence",
            "timestamp": "2023-01-01T00:00:00Z"
        }]
    }
    record_push_event(push_record, push_records=push_records, push_records_lock=push_records_lock)
    
    # 检查文件是否创建并包含数据
    assert os.path.exists(push_file)
    with open(push_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["project_name"] == "test-persist"

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