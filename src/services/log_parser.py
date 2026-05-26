import re
import logging
from typing import Dict, List, Optional

app_logger = logging.getLogger('app_logger')

ERROR_PATTERNS = [
    r'error:',
    r'failed',
    r'ERROR',
    r'Failed',
    r'Exception',
    r'exception:',
    r'FAILED',
    r'\bfail\b',
    r'command.*failed',
    r'exit code \d+',
]


def get_log_config():
    from src.config import get_config
    config = get_config()
    default_log_config = {
        'max_lines': 100,
        'error_context_lines': 5
    }
    log_config = config.get('log_config', default_log_config)
    return log_config.get('max_lines', 100), log_config.get('error_context_lines', 5)


def find_last_error(lines: List[str]) -> int:
    """
    从后往前查找最后一个错误所在行的索引

    Args:
        lines: 日志行列表

    Returns:
        int: 错误行索引，如果未找到返回 -1
    """
    compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in ERROR_PATTERNS]

    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if not line or not line.strip():
            continue

        for pattern in compiled_patterns:
            if pattern.search(line):
                app_logger.debug(f"log_parser | find_error | line={i}, pattern={line.strip()[:80]}")
                return i

    return -1


def parse_error_from_logs(log_content: str, context_lines: Optional[int] = None) -> Dict:
    """
    解析日志，提取最后错误信息

    Args:
        log_content: 原始日志内容
        context_lines: 错误上下文行数，如果为 None 则使用配置值

    Returns:
        dict: {
            'summary': '最后 100 行摘要',
            'error_detail': '最后一个错误的详情（含上下文）',
            'error_line': '错误行内容',
            'last_error_context': '最后错误上下文（用于通知）'
        }
    """
    if context_lines is None:
        _, context_lines = get_log_config()

    if not log_content:
        return {
            'summary': '',
            'error_detail': '',
            'error_line': '',
            'last_error_context': ''
        }

    lines = log_content.split('\n')
    max_lines, _ = get_log_config()

    summary_lines = lines[-max_lines:] if len(lines) > max_lines else lines
    summary = '\n'.join(summary_lines)

    error_index = find_last_error(lines)

    if error_index == -1:
        app_logger.info("log_parser | no_error_pattern | fallback=last_20_lines")
        fallback_lines = lines[-20:] if len(lines) > 20 else lines
        return {
            'summary': summary,
            'error_detail': '\n'.join(fallback_lines),
            'error_line': fallback_lines[-1] if fallback_lines else '',
            'last_error_context': '\n'.join(fallback_lines)
        }

    context_start = max(0, error_index - context_lines)
    context_end = min(len(lines), error_index + 1)

    context_lines_list = lines[context_start:context_end]

    error_line = lines[error_index]

    error_detail = '\n'.join(context_lines_list)

    last_error_context = f"...\n" + error_detail + "\n..."

    app_logger.info(f"log_parser | parse_result | error_line={error_line.strip()[:80] if error_line else '-'}")

    return {
        'summary': summary,
        'error_detail': error_detail,
        'error_line': error_line,
        'last_error_context': last_error_context
    }
