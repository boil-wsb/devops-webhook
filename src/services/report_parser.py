import re
import logging

logger = logging.getLogger('app_logger')


def parse_health_check_report(html_content):
    if not html_content:
        return None

    try:
        from bs4 import BeautifulSoup
        return _parse_with_bs4(html_content)
    except ImportError:
        logger.warning("bs4 不可用，使用正则解析")
        return _parse_with_regex(html_content)


def _parse_with_bs4(html_content):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')

    ok_count = 0
    warning_count = 0
    critical_count = 0

    for item in soup.find_all('div', class_='summary-item'):
        number_tag = item.find('span', class_='summary-number')
        label_tag = item.find('span', class_='summary-label')
        if not number_tag or not label_tag:
            continue
        number = int(number_tag.get_text(strip=True))
        label = label_tag.get_text(strip=True)
        if '正常' in label:
            ok_count = number
        elif '警告' in label:
            warning_count = number
        elif '严重' in label:
            critical_count = number

    warning_hosts = []
    critical_hosts = []

    for host_card in soup.find_all('div', class_='host-card'):
        host_name_tag = host_card.find('div', class_='host-name')
        host_status_tag = host_card.find('div', class_='host-status')
        if not host_name_tag or not host_status_tag:
            continue

        host_ip = host_name_tag.get_text(strip=True)
        status_classes = host_status_tag.get('class', [])
        host_status = 'ok'
        for cls in status_classes:
            if cls in ('warning', 'critical'):
                host_status = cls
                break

        if host_status == 'ok':
            continue

        failed_checks = []
        for check_item in host_card.find_all('div', class_='check-item'):
            check_name_tag = check_item.find('div', class_='check-name')
            check_details_tag = check_item.find('div', class_='check-details')
            check_status_tag = check_item.find('div', class_='check-status')

            if not check_status_tag:
                continue

            check_status_classes = check_status_tag.get('class', [])
            check_status = 'ok'
            for cls in check_status_classes:
                if cls in ('warning', 'critical'):
                    check_status = cls
                    break

            if check_status == 'ok':
                continue

            check_name = check_name_tag.get_text(strip=True) if check_name_tag else '未知'
            check_details = check_details_tag.get_text(strip=True) if check_details_tag else ''
            check_details = re.sub(r'\s+', ' ', check_details).strip()

            failed_checks.append({
                'name': check_name,
                'status': check_status,
                'details': check_details
            })

        host_info = {
            'ip': host_ip,
            'status': host_status,
            'failed_checks': failed_checks
        }

        if host_status == 'critical':
            critical_hosts.append(host_info)
        else:
            warning_hosts.append(host_info)

    footer_tag = soup.find('div', class_='footer')
    generate_time = ''
    if footer_tag:
        time_match = re.search(r'生成时间:\s*([\d\-T:.]+Z?)', footer_tag.get_text())
        if time_match:
            generate_time = time_match.group(1)

    return {
        'ok_count': ok_count,
        'warning_count': warning_count,
        'critical_count': critical_count,
        'total_count': ok_count + warning_count + critical_count,
        'warning_hosts': warning_hosts,
        'critical_hosts': critical_hosts,
        'generate_time': generate_time
    }


def _parse_with_regex(html_content):
    ok_count = 0
    warning_count = 0
    critical_count = 0

    summary_pattern = r'<div class="summary-item\s+(ok|warning|critical)">\s*<span class="summary-number">(\d+)</span>\s*<span class="summary-label">([^<]+)</span>'
    for match in re.finditer(summary_pattern, html_content):
        status, count_str, label = match.groups()
        count = int(count_str)
        if '正常' in label or status == 'ok':
            ok_count = count
        elif '警告' in label or status == 'warning':
            warning_count = count
        elif '严重' in label or status == 'critical':
            critical_count = count

    host_card_pattern = re.compile(
        r'<div class="host-card">(.*?)</div>\s*</div>\s*</div>\s*<div class="host-card">|<div class="host-card">(.*?)</div>\s*</div>\s*</div>\s*<div class="footer">',
        re.DOTALL
    )

    host_name_pattern = re.compile(r'<div class="host-name">([^<]+)</div>')
    host_status_pattern = re.compile(r'<div class="host-status\s+(ok|warning|critical)">')
    check_name_pattern = re.compile(r'<div class="check-name">\s*([^<]+?)\s*</div>', re.DOTALL)
    check_details_pattern = re.compile(r'<div class="check-details">\s*(.*?)\s*</div>', re.DOTALL)
    check_status_pattern = re.compile(r'<div class="check-status\s+(ok|warning|critical)">')

    warning_hosts = []
    critical_hosts = []

    host_blocks = re.split(r'<div class="host-card">', html_content)
    for block in host_blocks[1:]:
        name_match = host_name_pattern.search(block)
        status_match = host_status_pattern.search(block)
        if not name_match or not status_match:
            continue

        host_ip = name_match.group(1).strip()
        host_status = status_match.group(1)

        if host_status == 'ok':
            continue

        failed_checks = []
        check_blocks = re.split(r'<div class="check-item">', block)
        for check_block in check_blocks[1:]:
            cstatus_match = check_status_pattern.search(check_block)
            if not cstatus_match or cstatus_match.group(1) == 'ok':
                continue

            cname_match = check_name_pattern.search(check_block)
            cdetails_match = check_details_pattern.search(check_block)

            check_name = cname_match.group(1).strip() if cname_match else '未知'
            check_details = ''
            if cdetails_match:
                check_details = re.sub(r'<[^>]+>', '', cdetails_match.group(1))
                check_details = re.sub(r'\s+', ' ', check_details).strip()

            failed_checks.append({
                'name': check_name,
                'status': cstatus_match.group(1),
                'details': check_details
            })

        host_info = {
            'ip': host_ip,
            'status': host_status,
            'failed_checks': failed_checks
        }

        if host_status == 'critical':
            critical_hosts.append(host_info)
        else:
            warning_hosts.append(host_info)

    time_match = re.search(r'生成时间:\s*([\d\-T:.]+Z?)', html_content)
    generate_time = time_match.group(1) if time_match else ''

    return {
        'ok_count': ok_count,
        'warning_count': warning_count,
        'critical_count': critical_count,
        'total_count': ok_count + warning_count + critical_count,
        'warning_hosts': warning_hosts,
        'critical_hosts': critical_hosts,
        'generate_time': generate_time
    }


def build_inspection_card_elements(report_data, presigned_url, file_size_mb, report_path):
    if not report_data:
        return _build_fallback_elements(presigned_url, file_size_mb, report_path)

    ok_count = report_data.get('ok_count', 0)
    warning_count = report_data.get('warning_count', 0)
    critical_count = report_data.get('critical_count', 0)
    total_count = report_data.get('total_count', 0)
    warning_hosts = report_data.get('warning_hosts', [])
    critical_hosts = report_data.get('critical_hosts', [])
    generate_time = report_data.get('generate_time', '')

    if critical_count > 0:
        overall_status = '🔴 严重'
        template = 'red'
    elif warning_count > 0:
        overall_status = '🟡 警告'
        template = 'yellow'
    else:
        overall_status = '🟢 正常'
        template = 'green'

    summary_line = f"**巡检概览**：共 {total_count} 台主机 | ✅ 正常 {ok_count} | ⚠️ 警告 {warning_count} | 🔴 严重 {critical_count}"

    conclusion = f"**巡检结论**：{overall_status} — "
    if critical_count > 0:
        critical_ips = ', '.join(h['ip'] for h in critical_hosts)
        conclusion += f"{critical_count} 台主机存在严重问题（{critical_ips}），需立即处理"
    elif warning_count > 0:
        warning_ips = ', '.join(h['ip'] for h in warning_hosts)
        conclusion += f"{warning_count} 台主机存在警告（{warning_ips}），建议关注"
    else:
        conclusion += "所有主机运行正常，无异常"

    elements = []

    elements.append({
        "tag": "markdown",
        "content": summary_line
    })

    elements.append({
        "tag": "markdown",
        "content": conclusion
    })

    meta_parts = []
    if generate_time:
        meta_parts.append(f"🕐 巡检时间：{generate_time}")
    meta_parts.append(f"📁 报告大小：{file_size_mb} MB")
    meta_parts.append("⏱️ 下载链接有效期：2小时")
    elements.append({
        "tag": "markdown",
        "content": '\n'.join(meta_parts)
    })

    elements.append({"tag": "hr"})

    if presigned_url:
        elements.append({
            "tag": "button",
            "type": "primary",
            "text": {
                "tag": "plain_text",
                "content": "📥 下载完整报告"
            },
            "behaviors": [
                {
                    "type": "open_url",
                    "default_url": presigned_url,
                    "android_url": presigned_url,
                    "ios_url": presigned_url,
                    "pc_url": presigned_url
                }
            ]
        })

    return elements, template


def _build_fallback_elements(presigned_url, file_size_mb, report_path):
    import os
    elements = [
        {
            "tag": "markdown",
            "content": f"📁 报告路径：{report_path}\n📏 文件大小：{file_size_mb} MB\n⏱️ 有效期：2小时\n\n> ⚠️ **安全提醒**: 下载链接有效期为2小时，请尽快下载"
        },
        {"tag": "hr"}
    ]

    if presigned_url:
        elements.append({
            "tag": "button",
            "type": "primary",
            "text": {
                "tag": "plain_text",
                "content": "📥 下载报告"
            },
            "behaviors": [
                {
                    "type": "open_url",
                    "default_url": presigned_url,
                    "android_url": presigned_url,
                    "ios_url": presigned_url,
                    "pc_url": presigned_url
                }
            ]
        })

    return elements, 'blue'
