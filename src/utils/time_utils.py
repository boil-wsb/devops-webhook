from datetime import datetime, timedelta


def format_duration(seconds):
    """
    根据持续时间格式化为 秒 或 分秒
    :param seconds: 持续时间（单位：秒）
    :return: 格式化后的字符串
    """
    if seconds < 60:
        return f"{seconds}秒"
    else:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        return f"{minutes}分{remaining_seconds}秒"


def calculate_interval(start_time_str, end_time_str, time_format="%Y-%m-%d %H:%M:%S"):
    """
    计算开始时间和结束时间的间隔，并格式化输出
    :param start_time_str: 开始时间字符串，例如 "2024-12-18 12:00:00"
    :param end_time_str: 结束时间字符串，例如 "2024-12-18 14:30:45"
    :param time_format: 时间字符串格式，默认 "%Y-%m-%d %H:%M:%S"
    :return: 格式化的时间间隔
    """
    # 解析时间字符串为 datetime 对象
    start_time = datetime.strptime(start_time_str, time_format)
    end_time = datetime.strptime(end_time_str, time_format)
    # 计算时间间隔（timedelta 对象）
    delta = end_time - start_time
    seconds = delta.seconds
    return format_duration(seconds)


def convert_utc_to_utc8(utc_time_str):
    """
    将 UTC 时间字符串（格式：2024-12-18 12:53:35 UTC）转换为 UTC+8 时间
    :param utc_time_str: UTC 时间字符串，例如 "2024-12-18 12:53:35 UTC"
    :return: 转换后的 UTC+8 时间字符串
    """
    if not utc_time_str or not utc_time_str.strip():
        return None
    try:
        # 去掉 " UTC" 并将字符串解析为 datetime 对象
        if " UTC" in utc_time_str:
            time_part = utc_time_str.replace(" UTC", "")
            utc_time = datetime.strptime(time_part, "%Y-%m-%d %H:%M:%S")
        else:
            # 处理没有" UTC"后缀的情况
            utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
        # 添加 8 小时的偏移量
        utc8_time = utc_time + timedelta(hours=8)
        # 返回格式化后的 UTC+8 时间字符串
        return utc8_time.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        # 如果解析失败，尝试其他可能的格式
        try:
            # 尝试解析带微秒的时间格式
            utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S.%f")
            utc8_time = utc_time + timedelta(hours=8)
            return utc8_time.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            # 如果仍然失败，返回原始字符串
            return utc_time_str
