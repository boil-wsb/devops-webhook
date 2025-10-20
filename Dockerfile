# 使用官方 Python 基础镜像
FROM 192.168.100.213:8083/devops/python:3.9-slim

# 设置工作目录
WORKDIR /app

# 将当前目录的文件复制到容器内
COPY . .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 暴露 Flask 默认的端口
EXPOSE 8080

# 定义启动命令
CMD ["python", "app.py"]
