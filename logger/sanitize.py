import re
import logging

_SANITIZE_PATTERNS = [
    (re.compile(r'(password\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(ssh_password\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(secret_key\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(private_token\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(access_key\s*=\s*)\S+', re.IGNORECASE), r'\1***'),
    (re.compile(r'(open_id=)(ou_\w+)'), r'\1***'),
]


class SanitizeFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, str):
            for pattern, replacement in _SANITIZE_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True
