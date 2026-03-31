import sqlite3
import json
import os
import logging
import threading
import time
import atexit
from datetime import datetime, timedelta
from contextlib import contextmanager

app_logger = logging.getLogger('app_logger')

DB_PATH = 'webhook.db'
RETENTION_DAYS = 30

_connection_local = threading.local()
_lock = threading.Lock()
_connections_registry = set()
_shutdown_registered = False


def _register_connection(conn):
    """注册连接以便后续清理"""
    with _lock:
        _connections_registry.add(conn)


def _unregister_connection(conn):
    """取消注册连接"""
    with _lock:
        _connections_registry.discard(conn)


def close_all_connections():
    """关闭所有注册的数据库连接"""
    with _lock:
        for conn in _connections_registry.copy():
            try:
                conn.close()
                app_logger.debug(f"已关闭数据库连接: {id(conn)}")
            except Exception as e:
                app_logger.warning(f"关闭数据库连接失败: {e}")
        _connections_registry.clear()
    app_logger.info("所有数据库连接已关闭")


def _register_atexit():
    """注册进程退出时的清理函数"""
    global _shutdown_registered
    if not _shutdown_registered:
        atexit.register(close_all_connections)
        _shutdown_registered = True


def get_db_config():
    """获取数据库配置"""
    config_path = 'config.conf'
    if os.path.exists(config_path):
        try:
            import json
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
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


def get_connection():
    """获取线程本地的数据库连接"""
    _register_atexit()
    if not hasattr(_connection_local, 'connection'):
        config = get_db_config()
        db_path = config.get('db_path', DB_PATH)
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        if config.get('wal_mode', True):
            conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=30000')
        _connection_local.connection = conn
        _register_connection(conn)
    return _connection_local.connection


@contextmanager
def get_db_cursor():
    """获取数据库游标的上下文管理器"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()


def init_database():
    """初始化数据库，创建必要的表"""
    try:
        with get_db_cursor() as cursor:
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
                    created_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day')),
                    updated_at TEXT DEFAULT (datetime('now', '+8 hours', 'start of day'))
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

            try:
                cursor.execute('''
                    ALTER TABLE pipeline_records ADD COLUMN updated_at TEXT
                ''')
            except Exception:
                pass
            try:
                cursor.execute('''
                    ALTER TABLE push_records ADD COLUMN updated_at TEXT
                ''')
            except Exception:
                pass

        config = get_db_config()
        if config.get('wal_mode', True):
            with get_db_cursor() as cursor:
                cursor.execute('PRAGMA journal_mode=WAL')

        app_logger.info("数据库初始化完成")
    except Exception as e:
        app_logger.error(f"数据库初始化失败: {str(e)}")
        raise


def cleanup_old_records():
    """清理 30 天前的数据"""
    try:
        config = get_db_config()
        retention_days = config.get('retention_days', RETENTION_DAYS)

        with get_db_cursor() as cursor:
            cursor.execute('''
                DELETE FROM push_records
                WHERE created_at < datetime('now', '-{days} days', '+8 hours', 'start of day')
            '''.format(days=retention_days))

            deleted_push = cursor.rowcount

            cursor.execute('''
                DELETE FROM pipeline_records
                WHERE created_at < datetime('now', '-{days} days', '+8 hours', 'start of day')
            '''.format(days=retention_days))

            deleted_pipeline = cursor.rowcount

        app_logger.info(f"数据清理完成: push_records={deleted_push}, pipeline_records={deleted_pipeline}")
        return deleted_push, deleted_pipeline
    except Exception as e:
        app_logger.error(f"数据清理失败: {str(e)}")
        return 0, 0


def start_cleanup_thread():
    """启动定时清理线程"""
    def cleanup_loop():
        while True:
            try:
                config = get_db_config()
                cleanup_hour = config.get('cleanup_hour', 3)
                current_hour = datetime.now().hour

                if current_hour == cleanup_hour:
                    cleanup_old_records()
                    time.sleep(3600)
                else:
                    time.sleep(1800)
            except Exception as e:
                app_logger.error(f"清理线程异常: {str(e)}")
                time.sleep(1800)

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()
    app_logger.info("数据清理线程已启动")


class PushRecordDB:
    """Push 记录数据库操作类"""

    @staticmethod
    def insert(project_name, ref, user_name, git_url, subpath, push_time, commits):
        """插入 push 记录"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO push_records
                    (project_name, ref, user_name, git_url, subpath, push_time, commits)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (project_name, ref, user_name, git_url, subpath, push_time,
                      json.dumps(commits, ensure_ascii=False) if commits else None))
                return cursor.lastrowid
        except Exception as e:
            app_logger.error(f"插入 push 记录失败: {str(e)}")
            return None

    @staticmethod
    def get_all():
        """获取所有 push 记录"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('SELECT * FROM push_records ORDER BY push_time DESC')
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            app_logger.error(f"获取 push 记录失败: {str(e)}")
            return []

    @staticmethod
    def get_by_git_url(git_url, limit=10):
        """根据 git_url 获取最近的 push 记录"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM push_records
                    WHERE git_url = ?
                    ORDER BY push_time DESC
                    LIMIT ?
                ''', (git_url, limit))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            app_logger.error(f"获取 push 记录失败: {str(e)}")
            return []

    @staticmethod
    def get_cd_records(subpath=None, limit=1000):
        """获取 CD 记录（包含 pipeline_iid 的 push 记录）"""
        try:
            with get_db_cursor() as cursor:
                if subpath:
                    cursor.execute('''
                        SELECT * FROM push_records
                        WHERE subpath = ?
                          AND commits LIKE '%"pipeline_iid"%'
                        ORDER BY push_time DESC
                        LIMIT ?
                    ''', (subpath, limit))
                else:
                    cursor.execute('''
                        SELECT * FROM push_records
                        WHERE commits LIKE '%"pipeline_iid"%'
                        ORDER BY push_time DESC
                        LIMIT ?
                    ''', (limit,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            app_logger.error(f"获取 CD 记录失败: {str(e)}")
            return []

    @staticmethod
    def update_commit_pipeline_info(git_url, commit_url, pipeline_iid, pipeline_status,
                                    deploy_ip, stages, subpath):
        """更新 push 记录中 commit 的 pipeline 信息"""
        try:
            records = PushRecordDB.get_by_git_url(git_url)
            for record in records:
                commits = json.loads(record['commits']) if record['commits'] else []
                updated = False
                for commit in commits:
                    if commit.get('url') == commit_url:
                        commit['pipeline_iid'] = pipeline_iid
                        commit['pipeline_status'] = pipeline_status
                        if deploy_ip:
                            commit['deploy_ip'] = deploy_ip if isinstance(deploy_ip, list) else [deploy_ip]
                        if stages:
                            commit['stages'] = stages
                        updated = True
                        break

                if updated:
                    with get_db_cursor() as cursor:
                        cursor.execute('''
                            UPDATE push_records
                            SET commits = ?, subpath = ?
                            WHERE id = ?
                        ''', (json.dumps(commits, ensure_ascii=False), subpath, record['id']))
                    break
        except Exception as e:
            app_logger.error(f"更新 push 记录失败: {str(e)}")

    @staticmethod
    def batch_insert(records):
        """批量插入 push 记录"""
        try:
            with get_db_cursor() as cursor:
                data = [
                    (r['project_name'], r['ref'], r.get('user_name'), r.get('git_url'),
                     r.get('subpath'), r.get('push_time'), json.dumps(r.get('commits', []), ensure_ascii=False))
                    for r in records
                ]
                cursor.executemany('''
                    INSERT INTO push_records
                    (project_name, ref, user_name, git_url, subpath, push_time, commits)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', data)
                return cursor.rowcount
        except Exception as e:
            app_logger.error(f"批量插入 push 记录失败: {str(e)}")
            return 0

    @staticmethod
    def count():
        """获取 push 记录总数"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('SELECT COUNT(*) as count FROM push_records')
                return cursor.fetchone()['count']
        except Exception as e:
            app_logger.error(f"获取 push 记录总数失败: {str(e)}")
            return 0


class PipelineRecordDB:
    """Pipeline 记录数据库操作类"""

    @staticmethod
    def upsert(path_with_namespace, namespace, project_name, pipeline_path,
               pipeline_iid, git_url, subpath, latest_triggered_by, record_time,
               branch, commit_url):
        """插入或更新 pipeline 记录"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO pipeline_records
                    (path_with_namespace, namespace, project_name, pipeline_path,
                     pipeline_iid, git_url, subpath, latest_triggered_by,
                     record_time, branch, commit_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path_with_namespace) DO UPDATE SET
                        namespace = excluded.namespace,
                        project_name = excluded.project_name,
                        pipeline_path = excluded.pipeline_path,
                        pipeline_iid = excluded.pipeline_iid,
                        git_url = excluded.git_url,
                        subpath = excluded.subpath,
                        latest_triggered_by = excluded.latest_triggered_by,
                        record_time = excluded.record_time,
                        branch = excluded.branch,
                        commit_url = excluded.commit_url,
                        updated_at = datetime('now', '+8 hours')
                ''', (path_with_namespace, namespace, project_name, pipeline_path,
                      pipeline_iid, git_url, subpath, latest_triggered_by,
                      record_time, branch, commit_url))
                return True
        except Exception as e:
            app_logger.error(f"插入/更新 pipeline 记录失败: {str(e)}")
            return False

    @staticmethod
    def get_all():
        """获取所有 pipeline 记录"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('SELECT * FROM pipeline_records ORDER BY record_time DESC')
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            app_logger.error(f"获取 pipeline 记录失败: {str(e)}")
            return []

    @staticmethod
    def get_by_namespace(namespace):
        """根据 namespace 获取 pipeline 记录"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM pipeline_records
                    WHERE subpath = ? OR namespace = ?
                    ORDER BY record_time DESC
                ''', (namespace, namespace))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            app_logger.error(f"获取 pipeline 记录失败: {str(e)}")
            return []

    @staticmethod
    def batch_upsert(records):
        """批量插入或更新 pipeline 记录"""
        try:
            with get_db_cursor() as cursor:
                for r in records:
                    cursor.execute('''
                        INSERT INTO pipeline_records
                        (path_with_namespace, namespace, project_name, pipeline_path,
                         pipeline_iid, git_url, subpath, latest_triggered_by,
                         record_time, branch, commit_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path_with_namespace) DO UPDATE SET
                            namespace = excluded.namespace,
                            project_name = excluded.project_name,
                            pipeline_path = excluded.pipeline_path,
                            pipeline_iid = excluded.pipeline_iid,
                            git_url = excluded.git_url,
                            subpath = excluded.subpath,
                            latest_triggered_by = excluded.latest_triggered_by,
                            record_time = excluded.record_time,
                            branch = excluded.branch,
                            commit_url = excluded.commit_url
                    ''', (r['path_with_namespace'], r.get('namespace'), r.get('project_name'),
                          r.get('pipeline_path'), r.get('pipeline_iid'), r.get('git_url'),
                          r.get('subpath'), r.get('latest_triggered_by'), r.get('record_time'),
                          r.get('branch'), r.get('commit_url')))
                return True
        except Exception as e:
            app_logger.error(f"批量插入/更新 pipeline 记录失败: {str(e)}")
            return False

    @staticmethod
    def count():
        """获取 pipeline 记录总数"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('SELECT COUNT(*) as count FROM pipeline_records')
                return cursor.fetchone()['count']
        except Exception as e:
            app_logger.error(f"获取 pipeline 记录总数失败: {str(e)}")
            return 0


class MigrationHistoryDB:
    """迁移历史记录"""

    @staticmethod
    def record(source_file, records_migrated):
        """记录迁移历史"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('''
                    INSERT INTO migration_history (source_file, records_migrated)
                    VALUES (?, ?)
                ''', (source_file, records_migrated))
                return cursor.lastrowid
        except Exception as e:
            app_logger.error(f"记录迁移历史失败: {str(e)}")
            return None

    @staticmethod
    def get_all():
        """获取所有迁移历史"""
        try:
            with get_db_cursor() as cursor:
                cursor.execute('SELECT * FROM migration_history ORDER BY migrated_at DESC')
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            app_logger.error(f"获取迁移历史失败: {str(e)}")
            return []
