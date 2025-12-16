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
    将带有时区信息的时间字符串转换为 UTC+8 时间
    :param utc_time_str: 时间字符串，支持格式：
                         - "2024-12-18 12:53:35 UTC"
                         - "2024-12-18T12:53:35Z"
                         - "2024-12-18 12:53:35 +0800"
                         - "2024-12-18 12:53:35 -0400"
    :return: 转换后的 UTC+8 时间字符串，格式："%Y-%m-%d %H:%M:%S"（不含时区）
    """
    if not utc_time_str or not utc_time_str.strip():
        return None
    try:
        original_time_str = utc_time_str
        time_str = utc_time_str.strip()
        
        # 初始化时区偏移量（默认 UTC+0）
        timezone_offset = 0
        
        # 解析时区信息
        if " +" in time_str:
            # 处理带 +HHMM 或 +HH:MM 时区的情况
            time_part, tz_part = time_str.split(" +", 1)
            tz_part = tz_part.split()[0]  # 只取时区部分，忽略其他内容
            if len(tz_part) >= 4:
                # 解析时区偏移，如 +0800 或 +08:00
                if ":" in tz_part:
                    hours, minutes = map(int, tz_part.split(":"))
                else:
                    hours = int(tz_part[:2])
                    minutes = int(tz_part[2:4])
                timezone_offset = hours + minutes / 60
        elif " -" in time_str:
            # 处理带 -HHMM 或 -HH:MM 时区的情况
            time_part, tz_part = time_str.split(" -", 1)
            tz_part = tz_part.split()[0]  # 只取时区部分，忽略其他内容
            if len(tz_part) >= 4:
                # 解析时区偏移，如 -0400 或 -04:00
                if ":" in tz_part:
                    hours, minutes = map(int, tz_part.split(":"))
                else:
                    hours = int(tz_part[:2])
                    minutes = int(tz_part[2:4])
                timezone_offset = - (hours + minutes / 60)
        elif "Z" in time_str:
            # 处理带 Z 时区的情况（表示 UTC+0）
            time_part = time_str.replace("Z", "").replace("T", " ")
            timezone_offset = 0
        elif " UTC" in time_str:
            # 处理带 UTC 时区的情况（表示 UTC+0）
            time_part = time_str.replace(" UTC", "")
            timezone_offset = 0
        else:
            # 没有明确时区信息，默认按 UTC+0 处理
            time_part = time_str
            timezone_offset = 0
        
        # 处理不同的时间格式
        time_part = time_part.strip()
        if not time_part:
            return None
        
        # 解析时间部分
        try:
            if "." in time_part:
                # 处理带微秒的情况
                if "T" in time_part:
                    # ISO 格式带微秒，如 "2024-12-18T12:53:35.123"
                    dt = datetime.strptime(time_part, "%Y-%m-%dT%H:%M:%S.%f")
                else:
                    # 普通格式带微秒，如 "2024-12-18 12:53:35.123"
                    dt = datetime.strptime(time_part, "%Y-%m-%d %H:%M:%S.%f")
            else:
                if "T" in time_part:
                    # ISO 格式不带微秒，如 "2024-12-18T12:53:35"
                    dt = datetime.strptime(time_part, "%Y-%m-%dT%H:%M:%S")
                else:
                    # 普通格式不带微秒，如 "2024-12-18 12:53:35"
                    dt = datetime.strptime(time_part, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # 尝试其他可能的格式
            if "T" in time_part:
                # 尝试简化的 ISO 格式
                dt = datetime.strptime(time_part.replace("T", " "), "%Y-%m-%d %H:%M:%S")
            else:
                # 尝试其他常见格式
                dt = datetime.strptime(time_part, "%Y-%m-%d %H:%M:%S")
        
        # 计算 UTC 时间：将输入时间减去其时区偏移得到 UTC 时间
        utc_time = dt - timedelta(hours=timezone_offset)
        
        # 计算 UTC+8 时间：UTC 时间加上 8 小时偏移
        utc8_time = utc_time + timedelta(hours=8)
        
        # 返回格式化后的 UTC+8 时间字符串
        return utc8_time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        # 如果解析失败，返回原始字符串
        return utc_time_str
