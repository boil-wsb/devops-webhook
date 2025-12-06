import json
import requests

# 测试pipeline事件处理
print("测试pipeline事件处理")
print("=" * 50)

# 发送一个pipeline事件
print("\n发送pipeline事件...")
pipeline_payload = {
    "object_kind": "pipeline",
    "project": {
        "name": "test-project",
        "namespace": "test-namespace",
        "path_with_namespace": "test-namespace/test-project",
        "web_url": "http://192.168.23.19/test-namespace/test-project",
        "git_http_url": "http://192.168.23.19/test-namespace/test-project.git"
    },
    "object_attributes": {
        "status": "running",
        "id": 123,
        "iid": 456,
        "created_at": "2025-12-05T10:33:39Z",
        "ref": "main",
        "url": "http://192.168.23.19/test-namespace/test-project/-/pipelines/456",
        "source": "web"
    },
    "user": {
        "name": "test-user"
    },
    "commit": {
        "title": "test commit",
        "url": "http://192.168.23.19/test-namespace/test-project/-/commit/abc123"
    }
}

response = requests.post(
    'http://localhost:8080/vendor_bot/v2/DO',
    headers={'Content-Type': 'application/json'},
    json=pipeline_payload
)

print('测试请求状态码:', response.status_code)
print('测试请求响应:', response.text)

print("\n" + "=" * 50)
print("测试完成")
