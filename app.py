import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request
from src.routes import register_routes
from src.services import start_build_monitor
from logger import access_logger

app = Flask(__name__)

# 注册所有路由
register_routes(app)

# 添加HTTP访问日志记录器
@app.after_request
def log_access(response):
    """
    记录HTTP访问日志
    在每个请求处理完成后调用，记录请求的访问信息
    """
    # 从请求对象中提取信息
    remote_addr = request.remote_addr
    request_method = request.method
    path = request.path
    http_version = request.environ.get('SERVER_PROTOCOL', 'HTTP/1.1')
    status_code = response.status_code
    
    # 获取响应长度
    response_length = response.headers.get('Content-Length', 0)
    
    # 使用AccessLogger记录访问日志
    access_logger.log_access(remote_addr, request_method, path, http_version, status_code, response_length)
    
    return response

if __name__ == '__main__':
    # 启动构建监控线程
    start_build_monitor()
    
    # 启动主应用
    app.run(host='0.0.0.0', port=8080)
