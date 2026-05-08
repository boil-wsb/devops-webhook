import json
import os
import sys
import sqlite3
import logging
import yaml
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app_logger = logging.getLogger('app_logger')


def get_json_data(file_path):
    """读取 JSON 文件"""
    if not os.path.exists(file_path):
        app_logger.warning(f"文件不存在: {file_path}")
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            app_logger.info(f"成功读取 {file_path}, 记录数: {len(data) if isinstance(data, list) else 'N/A'}")
            return data
    except Exception as e:
        app_logger.error(f"读取 {file_path} 失败: {str(e)}")
        return []


def get_db_config():
    config_path = 'config.yaml'
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                return config.get('db_config', {
                    'db_path': 'webhook.db',
                    'wal_mode': True,
                    'retention_days': 30,
                    'cleanup_hour': 3
                })
        except Exception:
            pass
    return {
        'db_path': 'webhook.db',
        'wal_mode': True,
        'retention_days': 30,
        'cleanup_hour': 3
    }


def init_database(db_path):
    """初始化数据库"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS push_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            ref TEXT NOT NULL,
            user_name TEXT,
            git_url TEXT,
            subpath TEXT,
            push_time TEXT,
            commits TEXT,
            created_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day'))
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_push_project_time
        ON push_records(project_name, push_time)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_push_git_url
        ON push_records(git_url)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pipeline_records (
            path_with_namespace TEXT PRIMARY KEY,
            namespace TEXT,
            project_name TEXT,
            pipeline_path TEXT,
            pipeline_iid INTEGER,
            git_url TEXT,
            subpath TEXT,
            latest_triggered_by TEXT,
            record_time TEXT,
            branch TEXT,
            commit_url TEXT,
            created_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS migration_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT,
            records_migrated INTEGER,
            migrated_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day'))
        )
    ''')

    conn.commit()
    conn.close()
    # app_logger.info("数据库初始化完成")


def migrate_push_records(push_records_data, db_path):
    """迁移 push_records.json"""
    if not push_records_data:
        app_logger.info("push_records.json 无数据可迁移")
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    migrated_count = 0
    for record in push_records_data:
        try:
            commits = record.get('commits', [])
            cursor.execute('''
                INSERT INTO push_records
                (project_name, ref, user_name, git_url, subpath, push_time, commits)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.get('project_name'),
                record.get('ref'),
                record.get('user_name'),
                record.get('git_url'),
                record.get('subpath'),
                record.get('push_time'),
                json.dumps(commits, ensure_ascii=False) if commits else None
            ))
            migrated_count += 1
        except Exception as e:
            app_logger.error(f"迁移 push 记录失败: {str(e)}")

    conn.commit()
    conn.close()
    app_logger.info(f"push_records 迁移完成，共 {migrated_count} 条记录")
    return migrated_count


def migrate_pipeline_records(project_data, db_path):
    """迁移 project.json"""
    if not project_data:
        app_logger.info("project.json 无数据可迁移")
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    migrated_count = 0
    for key, record in project_data.items():
        try:
            cursor.execute('''
                INSERT INTO pipeline_records
                (path_with_namespace, namespace, project_name, pipeline_path,
                 pipeline_iid, git_url, subpath, latest_triggered_by,
                 record_time, branch, commit_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                key,
                record.get('namespace'),
                record.get('project_name'),
                record.get('pipeline_path'),
                record.get('pipeline_iid'),
                record.get('git_url'),
                record.get('subpath'),
                record.get('latest_triggered_by'),
                record.get('record_time'),
                record.get('branch'),
                record.get('commit_url')
            ))
            migrated_count += 1
        except Exception as e:
            app_logger.error(f"迁移 pipeline 记录失败 (key={key}): {str(e)}")

    conn.commit()
    conn.close()
    app_logger.info(f"pipeline_records 迁移完成，共 {migrated_count} 条记录")
    return migrated_count


def record_migration_history(source_file, records_migrated, db_path):
    """记录迁移历史"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute('''
            INSERT INTO migration_history (source_file, records_migrated)
            VALUES (?, ?)
        ''', (source_file, records_migrated))
        conn.commit()
    except Exception as e:
        app_logger.error(f"记录迁移历史失败: {str(e)}")

    conn.close()


def run_migration():
    """执行迁移"""
    print("=" * 60)
    print("SQLite 数据迁移工具")
    print("=" * 60)

    config = get_db_config()
    db_path = config.get('db_path', 'webhook.db')

    print(f"\n数据库路径: {db_path}")
    print(f"保留天数: {config.get('retention_days', 30)}")

    print("\n[1/5] 初始化数据库...")
    init_database(db_path)

    print("\n[2/5] 读取 push_records.json...")
    push_records_data = get_json_data('push_records.json')

    print("\n[3/5] 读取 project.json...")
    project_data = get_json_data('project.json')
    if project_data and not isinstance(project_data, dict):
        app_logger.warning("project.json 格式异常，期望 dict 结构")

    print("\n[4/5] 执行数据迁移...")

    start_time = datetime.now()

    push_count = migrate_push_records(push_records_data, db_path)
    record_migration_history('push_records.json', push_count, db_path)

    project_count = migrate_pipeline_records(project_data if isinstance(project_data, dict) else {}, db_path)
    record_migration_history('project.json', project_count, db_path)

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    print("\n[5/5] 迁移完成!")
    print("=" * 60)
    print(f"push_records.json: {push_count} 条记录")
    print(f"project.json: {project_count} 条记录")
    print(f"耗时: {duration:.2f} 秒")
    print("=" * 60)

    backup_dir = 'backup_json'
    if os.path.exists('push_records.json') or os.path.exists('project.json'):
        print(f"\n注意: 原 JSON 文件已保留在原位置")
        print(f"      如确认迁移成功，可手动删除:")
        if os.path.exists('push_records.json'):
            print(f"        - push_records.json")
        if os.path.exists('project.json'):
            print(f"        - project.json")

    return {
        'push_count': push_count,
        'project_count': project_count,
        'duration': duration
    }


if __name__ == '__main__':
    result = run_migration()
    sys.exit(0)
