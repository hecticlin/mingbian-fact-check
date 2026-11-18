"""
utils/db.py - 数据库连接池封装

"""
from __future__ import annotations

import os
import pymysql
from dbutils.pooled_db import PooledDB

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'mingbian'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'mingbian'),
    'charset':  'utf8mb4',
    'connect_timeout': 10,
    'read_timeout':    30,
    'write_timeout':   30,
    'autocommit': False,
}

# 全局连接池（应用生命周期内只初始化一次）
_pool: PooledDB | None = None


def _get_pool() -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=20,
            mincached=2,
            maxcached=5,
            maxshared=0,
            blocking=True,
            ping=1,
            cursorclass=pymysql.cursors.DictCursor,
            **DB_CONFIG,
        )
    return _pool


def _get_conn():
    """从连接池借出一条连接"""
    return _get_pool().connection()


# QueryResult：execute_query 的返回值

class QueryResult:
    """
    封装 execute_query 执行结果。
    在连接归还前缓存 lastrowid 和 rowcount，避免连接归还后 cursor 失效。
    支持布尔判断：if result: ...
    """
    __slots__ = ('lastrowid', 'rowcount')

    def __init__(self, lastrowid: int, rowcount: int):
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def __bool__(self):
        return True


# Database 类

class Database:
    """
    保持与原版完全相同的对外接口。

    connect() / close() 空操作，保持接口兼容，
    实际连接管理完全由连接池负责。
    """

    def __init__(self, **kwargs):
        pass

    # 兼容旧调用

    @property
    def connection(self):
        """
        路由中 `if db.connection:` 判断。
        返回 True/False 表示连接池是否可用。
        注意：不再返回代理对象，不要对其调用 .commit()/.rollback()/.cursor()。
        """
        try:
            _get_pool()
            return True
        except Exception:
            return False

    def connect(self):
        """连接池自动管理"""
        return self

    def close(self):
        """连接池自动管理"""
        pass

    # 查询操作

    def fetch_one(self, query, params=None):
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor() as cur:
                cur.execute(query, params or ())
                return cur.fetchone()
        except Exception as e:
            print(f"fetch_one 失败: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def fetch_all(self, query, params=None):
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor() as cur:
                cur.execute(query, params or ())
                return cur.fetchall()
        except Exception as e:
            print(f"fetch_all 失败: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def execute_query(self, query, params=None):
        """
        执行写操作，自动 commit，返回 QueryResult（含 lastrowid、rowcount）。
        失败时自动 rollback 并返回 None。
        """
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor() as cur:
                cur.execute(query, params or ())
                result = QueryResult(lastrowid=cur.lastrowid, rowcount=cur.rowcount)
            conn.commit()
            return result
        except Exception as e:
            print(f"execute_query 失败: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return None
        finally:
            if conn:
                conn.close()

    def execute(self, query, params=None):
        """执行写操作，返回 True/False"""
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor() as cur:
                cur.execute(query, params or ())
            conn.commit()
            return True
        except Exception as e:
            print(f"execute 失败: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
        finally:
            if conn:
                conn.close()

    def commit(self):
        """保留接口，execute/execute_query 内部已自动 commit"""
        pass

    def check_table_exists(self, table_name):
        conn = None
        try:
            conn = _get_conn()
            with conn.cursor() as cur:
                cur.execute("SHOW TABLES LIKE %s", (table_name,))
                return cur.fetchone() is not None
        except Exception as e:
            print(f"check_table_exists 失败: {e}")
            return False
        finally:
            if conn:
                conn.close()


# 全局实例
db = Database(**DB_CONFIG)


# 兼容旧版导入
def _make_connection():
    """
    从连接池借出一条连接。
    调用方用完后务必调用 conn.close() 归还到池中。
    """
    return _get_conn()
