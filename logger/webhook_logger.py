import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from logger.context import request_id_var, project_var, pipeline_var, route_var
from logger.sanitize import SanitizeFilter


class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get('') or '-'
        record.project_name = project_var.get('') or '-'
        record.pipeline_iid = pipeline_var.get('') or '-'
        record.route_name = route_var.get('') or '-'
        return True


def _load_log_level(logger_name):
    try:
        from src.config import get_config
        config = get_config()
        log_config = config.get('log_config', {})
        global_level = getattr(logging, log_config.get('level', 'INFO').upper(), logging.INFO)
        module_levels = log_config.get('module_levels', {})
        if logger_name in module_levels:
            return getattr(logging, module_levels[logger_name].upper(), global_level)
        return global_level
    except Exception:
        return logging.INFO


class BaseLogger:
    _instances = {}

    def __new__(cls, log_dir='logs', log_name='base_logger'):
        if cls not in cls._instances:
            cls._instances[cls] = super(BaseLogger, cls).__new__(cls)
            cls._instances[cls]._initialize(log_dir, log_name)
        return cls._instances[cls]

    def _initialize(self, log_dir, log_name):
        self.log_dir = log_dir
        self.log_name = log_name
        self._ensure_log_directory_exists()
        self.logger = self._configure_logger()

    def _ensure_log_directory_exists(self):
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
        except Exception as e:
            logging.warning(f"unable to create log directory {self.log_dir}: {str(e)}")
            self.log_dir = '.'

    def _configure_logger(self):
        raise NotImplementedError("Subclasses must implement _configure_logger method")

    def _create_base_formatter(self):
        return logging.Formatter(
            '%(asctime)s.%(msecs)03d - %(levelname)s - [req_id=%(request_id)s] - %(name)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    def _add_filters_and_formatter(self, handler, formatter=None):
        if formatter is None:
            formatter = self._create_base_formatter()
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        handler.addFilter(SanitizeFilter())

    def _create_file_handler(self, logger_name):
        import re
        import time

        log_file = os.path.join(self.log_dir, f'{self.log_name}.log')

        file_handler = TimedRotatingFileHandler(
            filename=log_file,
            when='midnight',
            interval=1,
            backupCount=12,
            encoding='utf-8',
            delay=True
        )
        file_handler.suffix = "%Y-%m"
        file_handler.extMatch = re.compile(r"^\.\d{4}-\d{2}(\.gz)?$")

        original_should_rollover = file_handler.shouldRollover

        def custom_should_rollover(record):
            current_time = int(time.time())
            current_date = time.localtime(current_time)

            if current_date.tm_mday != 1:
                return False

            try:
                file_mtime = os.path.getmtime(file_handler.baseFilename)
                file_date = time.localtime(file_mtime)

                if file_date.tm_year == current_date.tm_year and file_date.tm_mon == current_date.tm_mon:
                    return False

                return True
            except (OSError, IOError):
                return False

        file_handler.shouldRollover = custom_should_rollover

        return file_handler

    def _create_console_handler(self):
        return logging.StreamHandler()

    def _prepare_log_entry(self, route_name, request_headers, request_body):
        return {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            'route': route_name,
            'headers': dict(request_headers),
            'body': request_body
        }


class WebhookLogger(BaseLogger):

    def __new__(cls, log_dir='logs', log_name='webhook_backup'):
        return super().__new__(cls, log_dir, log_name)

    def _configure_logger(self):
        logger = logging.getLogger('webhook_logger')
        logger.setLevel(_load_log_level('webhook_logger'))

        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        formatter = self._create_base_formatter()

        file_handler = self._create_file_handler('webhook_logger')
        self._add_filters_and_formatter(file_handler, formatter)
        logger.addHandler(file_handler)

        console_handler = self._create_console_handler()
        self._add_filters_and_formatter(console_handler, formatter)
        logger.addHandler(console_handler)

        return logger

    def log_request(self, route_name, request_headers, request_body):
        try:
            log_entry = self._prepare_log_entry(route_name, request_headers, request_body)
            self.logger.info(str(log_entry))
        except Exception as e:
            logging.error(f"webhook_log_write_failed | error={e}")


class MonitorLogger(BaseLogger):

    def __new__(cls, log_dir='logs', log_name='monitor_event'):
        return super().__new__(cls, log_dir, log_name)

    def _configure_logger(self):
        logger = logging.getLogger('monitor_event_logger')
        logger.setLevel(_load_log_level('monitor_event_logger'))

        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        formatter = self._create_base_formatter()

        file_handler = self._create_file_handler('monitor_event_logger')
        self._add_filters_and_formatter(file_handler, formatter)
        logger.addHandler(file_handler)

        console_handler = self._create_console_handler()
        self._add_filters_and_formatter(console_handler, formatter)
        logger.addHandler(console_handler)

        return logger

    def log_event(self, route_name, request_headers, request_body):
        try:
            log_entry = self._prepare_log_entry(route_name, request_headers, request_body)
            self.logger.info(str(log_entry))
        except Exception as e:
            logging.error(f"monitor_log_write_failed | error={e}")


class AccessLogger(BaseLogger):

    def __new__(cls, log_dir='logs', log_name='access'):
        return super().__new__(cls, log_dir, log_name)

    def _configure_logger(self):
        logger = logging.getLogger('access_logger')
        logger.setLevel(_load_log_level('access_logger'))

        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        formatter = self._create_base_formatter()

        file_handler = self._create_file_handler('access_logger')
        self._add_filters_and_formatter(file_handler, formatter)
        logger.addHandler(file_handler)

        return logger

    def log_access(self, remote_addr, request_method, path, http_version, status_code, response_length):
        try:
            log_entry = f"{remote_addr} - - [{datetime.now().strftime('%d/%b/%Y %H:%M:%S')}] \"{request_method} {path} {http_version}\" {status_code} {response_length}"
            self.logger.info(log_entry)
        except Exception as e:
            logging.error(f"access_log_write_failed | error={e}")


class AppLogger(BaseLogger):

    def __new__(cls, log_dir='logs', log_name='app'):
        return super().__new__(cls, log_dir, log_name)

    def _configure_logger(self):
        logger = logging.getLogger('app_logger')
        logger.setLevel(_load_log_level('app_logger'))

        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        formatter = self._create_base_formatter()

        file_handler = self._create_file_handler('app_logger')
        self._add_filters_and_formatter(file_handler, formatter)
        logger.addHandler(file_handler)

        console_handler = self._create_console_handler()
        self._add_filters_and_formatter(console_handler, formatter)
        logger.addHandler(console_handler)

        return logger

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)

    def debug(self, message):
        self.logger.debug(message)


monitor_logger = None
webhook_logger = None
access_logger = None
app_logger = None


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


_init_loggers()
