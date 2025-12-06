import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# 导入app_logger用于记录日志系统本身的错误

class BaseLogger:
    """
    日志处理器基类
    提供日志系统的通用功能，包括目录创建、日志配置等
    """
    _instances = {}  # 使用字典存储每个子类的实例
    
    def __new__(cls, log_dir='logs', log_name='base_logger'):
        # 单例模式实现，每个子类维护自己的实例
        if cls not in cls._instances:
            cls._instances[cls] = super(BaseLogger, cls).__new__(cls)
            cls._instances[cls]._initialize(log_dir, log_name)
        return cls._instances[cls]
    
    def _initialize(self, log_dir, log_name):
        """
        初始化日志系统
        """
        self.log_dir = log_dir
        self.log_name = log_name
        # 确保日志目录存在
        self._ensure_log_directory_exists()
        # 配置日志记录器
        self.logger = self._configure_logger()
    
    def _ensure_log_directory_exists(self):
        """
        确保日志目录存在，如果不存在则创建
        """
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
        except Exception as e:
            logging.warning(f"Warning: Failed to create log directory {self.log_dir}: {str(e)}")
            # 如果无法创建目录，使用当前目录作为备选
            self.log_dir = '.'
    
    def _configure_logger(self):
        """
        配置日志记录器的基础方法
        子类应该重写此方法以实现特定的日志配置
        """
        raise NotImplementedError("Subclasses must implement _configure_logger method")
    
    def _create_base_formatter(self):
        """
        创建基础的日志格式器
        """
        return logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    def _create_file_handler(self, logger_name):
        """
        创建按月滚动的文件处理器
        每月午夜切换日志文件，每月一个新文件，保留12个月
        """
        import re
        log_file = os.path.join(self.log_dir, f'{self.log_name}.log')
        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when='M',         # 按月滚动
            interval=1,       # 每1个月一个新文件
            backupCount=12,   # 保留12个月的日志
            encoding='utf-8',
            delay=True        # 延迟打开文件，避免覆盖已存在的日志文件
        )
        # 设置文件名的日期格式为月份
        file_handler.suffix = "%Y-%m"
        # 设置匹配后缀的正则表达式，确保正确识别已存在的备份文件
        # 匹配格式为 .2025-12 的后缀
        file_handler.extMatch = re.compile(r"^\.\d{4}-\d{2}$")
        
        return file_handler
    
    def _create_console_handler(self):
        """
        创建控制台处理器
        """
        return logging.StreamHandler()
    
    def _prepare_log_entry(self, route_name, request_headers, request_body):
        """
        准备日志条目的通用格式
        """
        return {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            'route': route_name,
            'headers': dict(request_headers),
            'body': request_body
        }


class WebhookLogger(BaseLogger):
    """
    Webhook请求日志处理器
    用于记录webhook请求的详细信息，支持按日期滚动日志文件
    """
    
    def __new__(cls, log_dir='logs', log_name='webhook_backup'):
        # 重写__new__方法以使用自定义的日志名称
        return super().__new__(cls, log_dir, log_name)
    
    def _configure_logger(self):
        """
        配置日志记录器，设置按日期滚动的文件处理器和控制台输出
        """
        logger = logging.getLogger('webhook_logger')
        logger.setLevel(logging.INFO)
        
        # 清除已有的处理器，避免重复添加
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # 创建文件处理器
        file_handler = self._create_file_handler('webhook_logger')
        formatter = self._create_base_formatter()
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 添加控制台输出
        console_handler = self._create_console_handler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger
    
    def log_request(self, route_name, request_headers, request_body):
        """
        记录webhook请求信息
        Args:
            route_name: 路由名称
            request_headers: 请求头信息
            request_body: 请求体内容
        """
        try:
            # 使用基类方法准备日志条目
            log_entry = self._prepare_log_entry(route_name, request_headers, request_body)
            # 将日志条目转换为JSON并记录
            self.logger.info(str(log_entry))
        except Exception as e:
            # 日志记录失败时的错误处理
            logging.error(f"Error writing webhook log: {str(e)}")

class MonitorLogger(BaseLogger):
    """
    监控事件日志处理器
    专门用于记录监控事件，同时输出到日志文件和控制台
    """
    def __new__(cls, log_dir='logs', log_name='monitor_event'):
        # 重写__new__方法以使用自定义的日志名称
        return super().__new__(cls, log_dir, log_name)

    def _configure_logger(self):
        """
        配置日志记录器，设置文件处理器和控制台输出
        """
        logger = logging.getLogger('monitor_event_logger')
        logger.setLevel(logging.INFO)
        # 清除已有的处理器，避免重复添加
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        # 创建文件处理器
        file_handler = self._create_file_handler('monitor_event_logger')
        formatter = self._create_base_formatter()
        file_handler.setFormatter(formatter)
        # 添加文件处理器到logger
        logger.addHandler(file_handler)
        
        # 添加控制台输出
        console_handler = self._create_console_handler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger
    
    def log_event(self, route_name, request_headers, request_body):
        """
        记录监控事件信息
        Args:
            route_name: 路由名称
            request_headers: 请求头信息
            request_body: 请求体内容
        """
        try:
            # 使用基类方法准备日志条目
            log_entry = self._prepare_log_entry(route_name, request_headers, request_body)
            
            # 将日志条目转换为JSON并记录
            self.logger.info(str(log_entry))
        except Exception as e:
            # 日志记录失败时的错误处理
            logging.error(f"Error writing monitor event log: {str(e)}")


class AccessLogger(BaseLogger):
    """
    HTTP访问日志处理器
    用于记录HTTP请求的访问日志，格式与Apache访问日志类似
    """
    
    def __new__(cls, log_dir='logs', log_name='access'):
        # 重写__new__方法以使用自定义的日志名称
        return super().__new__(cls, log_dir, log_name)
    
    def _create_access_formatter(self):
        """
        创建访问日志的专用格式器
        只记录原始日志条目，不添加额外的时间戳和级别信息
        """
        return logging.Formatter('%(message)s')
    
    def _configure_logger(self):
        """
        配置日志记录器，设置按日期滚动的文件处理器
        """
        logger = logging.getLogger('access_logger')
        logger.setLevel(logging.INFO)
        
        # 清除已有的处理器，避免重复添加
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # 创建文件处理器
        file_handler = self._create_file_handler('access_logger')
        # 使用访问日志专用格式器，只记录原始日志条目
        formatter = self._create_access_formatter()
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 不添加控制台输出，只记录到文件
        
        return logger
    
    def log_access(self, remote_addr, request_method, path, http_version, status_code, response_length):
        """
        记录HTTP访问日志
        Args:
            remote_addr: 客户端IP地址
            request_method: HTTP请求方法 (GET, POST等)
            path: 请求路径
            http_version: HTTP版本
            status_code: HTTP状态码
            response_length: 响应长度
        """
        try:
            # 格式化日志条目，匹配Apache访问日志格式
            log_entry = f"{remote_addr} - - [{datetime.now().strftime('%d/%b/%Y %H:%M:%S')}] \"{request_method} {path} {http_version}\" {status_code} {response_length}"
            # 记录日志
            self.logger.info(log_entry)
        except Exception as e:
            # 日志记录失败时的错误处理
            logging.error(f"Error writing access log: {str(e)}")


class AppLogger(BaseLogger):
    """
    应用程序通用日志处理器
    用于记录应用程序的一般日志信息，支持按日期滚动日志文件
    """
    
    def __new__(cls, log_dir='logs', log_name='app'):
        # 重写__new__方法以使用自定义的日志名称
        return super().__new__(cls, log_dir, log_name)
    
    def _configure_logger(self):
        """
        配置日志记录器，设置按日期滚动的文件处理器和控制台输出
        """
        logger = logging.getLogger('app_logger')
        logger.setLevel(logging.INFO)
        
        # 清除已有的处理器，避免重复添加
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # 创建文件处理器
        file_handler = self._create_file_handler('app_logger')
        formatter = self._create_base_formatter()
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 添加控制台输出
        console_handler = self._create_console_handler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger
    
    def info(self, message):
        """
        记录信息级别的日志
        Args:
            message: 日志信息
        """
        self.logger.info(message)
    
    def warning(self, message):
        """
        记录警告级别的日志
        Args:
            message: 日志信息
        """
        self.logger.warning(message)
    
    def error(self, message):
        """
        记录错误级别的日志
        Args:
            message: 日志信息
        """
        self.logger.error(message)
    
    def debug(self, message):
        """
        记录调试级别的日志
        Args:
            message: 日志信息
        """
        self.logger.debug(message)

# 延迟创建全局日志实例，避免循环导入问题
monitor_logger = None
webhook_logger = None
access_logger = None
app_logger = None

# 在模块导入完成后再创建实例
def _init_loggers():
    global monitor_logger, webhook_logger, access_logger, app_logger
    if not monitor_logger:
        monitor_logger = MonitorLogger()
    if not webhook_logger:
        webhook_logger = WebhookLogger()
    if not access_logger:
        access_logger = AccessLogger()
    if not app_logger:
        app_logger = AppLogger()

# 初始化日志实例
_init_loggers()