# 使用官方 Python 基础镜像
FROM 192.168.100.213:8083/devops/python:3.9-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（不再需要 docker 和 mc，docker 从宿主机挂载，MinIO 使用 Python SDK）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 将当前目录的文件复制到容器内
COPY . .

# 安装依赖
RUN pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ && pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 暴露 Flask 默认的端口
EXPOSE 8080

# 定义启动命令
CMD ["python", "app.py"]
