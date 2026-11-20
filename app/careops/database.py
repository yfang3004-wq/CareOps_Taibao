"""Small single-service datastore. SQLite preview and PostgreSQL adapter.
A transaction mutex deliberately serializes writes. Not a scale-out architecture.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Integer,
                        Boolean, Text, JSON, ForeignKey, DateTime, select, event, inspect)
from sqlalchemy.dialects.postgresql import JSONB

meta = MetaData()
js = JSON().with_variant(JSONB(), "postgresql")
locks = Table("service_lock", meta, Column("id", Integer, primary_key=True))
versions = Table("schema_versions", meta, Column("version", Integer, primary_key=True),
                 Column("applied_at", String(40), nullable=False))
facilities = Table("facilities", meta,
    Column("id", String(64), primary_key=True), Column("name", String(160), nullable=False),
    Column("timezone", String(80), nullable=False))
users = Table("users", meta,
    Column("id", String(48), primary_key=True), Column("username", String(80), unique=True, nullable=False),
    Column("display_name", String(80), nullable=False), Column("password_hash", Text, nullable=False),
    Column("facility_id", String(64), ForeignKey("facilities.id"), nullable=False),
    Column("role", String(32), nullable=False), Column("units", js, nullable=False),
    Column("active", Boolean, nullable=False), Column("created_at", String(40), nullable=False))
sessions = Table("sessions", meta,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(48), ForeignKey("users.id"), nullable=False),
    Column("created", Integer, nullable=False), Column("last_seen", Integer, nullable=False),
    Column("expires", Integer, nullable=False))
workspaces = Table("workspaces", meta,
    Column("facility_id", String(64), ForeignKey("facilities.id"), primary_key=True),
    Column("revision", Integer, nullable=False), Column("payload", js, nullable=False),
    Column("updated_at", String(40), nullable=False))
receipts = Table("command_receipts", meta,
    Column("facility_id", String(64), ForeignKey("facilities.id"), primary_key=True),
    Column("request_id", String(80), primary_key=True), Column("user_id", String(48), nullable=False),
    Column("digest", String(64), nullable=False), Column("result", js, nullable=False))
audit = Table("server_audit", meta,
    Column("id", String(48), primary_key=True), Column("facility_id", String(64), nullable=False),
    Column("user_id", String(48), nullable=False), Column("action", String(100), nullable=False),
    Column("entity_id", String(100), nullable=False), Column("outcome", String(20), nullable=False),
    Column("at", String(40), nullable=False), Column("request_id", String(80), nullable=False),
    Column("summary", js, nullable=False))
previews = Table("import_previews", meta,
    Column("id", String(48), primary_key=True), Column("facility_id", String(64), nullable=False),
    Column("user_id", String(48), nullable=False), Column("digest", String(64), nullable=False),
    Column("raw_text", Text, nullable=False), Column("normalized", js, nullable=False),
    Column("report", js, nullable=False), Column("expires", Integer, nullable=False),
    Column("committed", Boolean, nullable=False))
login_attempts = Table("login_attempts", meta,
    Column("id", String(48), primary_key=True), Column("account_key", String(64), nullable=False),
    Column("ip_key", String(64), nullable=False), Column("at", Integer, nullable=False))

# Account-private server drafts. Never included in a business JSON export.
drafts = Table("user_drafts", meta,
    Column("id", String(48), primary_key=True),
    Column("facility_id", String(64), ForeignKey("facilities.id"), nullable=False),
    Column("user_id", String(48), ForeignKey("users.id"), nullable=False),
    Column("kind", String(20), nullable=False), Column("context", js, nullable=False),
    Column("values", js, nullable=False), Column("revision", Integer, nullable=False),
    Column("workspace_revision", Integer, nullable=False), Column("scope_hash", String(64), nullable=False),
    Column("updated_at", String(40), nullable=False), Column("expires", Integer, nullable=False),
    Column("result", JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"), nullable=True))

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

class Database:
    def __init__(self, url: str):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 20}} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, **kwargs)
        self.sqlite = self.engine.dialect.name == "sqlite"
        if self.sqlite:
            @event.listens_for(self.engine, "connect")
            def sqlite_config(conn, _):
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=20000")
        elif self.engine.dialect.name != "postgresql":
            raise ValueError("只支持 SQLite 本地预览或 PostgreSQL")

    def initialize(self):
        # Refuse unknown/future/partial databases BEFORE any DDL is executed.
        existing = set(inspect(self.engine).get_table_names())
        if existing:
            if "schema_versions" not in existing:
                raise RuntimeError("已有数据库不是已识别的 CareOps 数据库；拒绝修改")
            with self.engine.connect() as c:
                found = c.execute(select(versions.c.version)).scalars().all()
            if found != [2] or not set(meta.tables).issubset(existing):
                raise RuntimeError("数据库结构版本不兼容或不完整；旧schema=1须先使用M3备份迁移脚本，不得覆盖旧库")
            inspector=inspect(self.engine)
            for name,table in meta.tables.items():
                if not {col.name for col in table.columns}.issubset({col['name'] for col in inspector.get_columns(name)}):
                    raise RuntimeError("数据库字段不完整；不得覆盖旧库")
        meta.create_all(self.engine)
        with self.engine.begin() as c:
            got = c.execute(select(versions.c.version)).scalars().all()
            if got and got != [2]:
                raise RuntimeError("数据库结构版本不兼容；不得覆盖旧库")
            if not got:
                c.execute(versions.insert().values(version=2, applied_at=utcnow()))
            if not c.execute(select(locks.c.id)).first():
                c.execute(locks.insert().values(id=1))
            if self.sqlite:
                for action in ("UPDATE", "DELETE"):
                    c.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS audit_no_{action.lower()} BEFORE {action} ON server_audit BEGIN SELECT RAISE(ABORT,'server_audit is append-only'); END")
            else:
                c.exec_driver_sql("""CREATE OR REPLACE FUNCTION careops_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'server_audit is append-only'; END $$""")
                c.exec_driver_sql("DROP TRIGGER IF EXISTS careops_audit_no_change ON server_audit")
                c.exec_driver_sql("CREATE TRIGGER careops_audit_no_change BEFORE UPDATE OR DELETE ON server_audit FOR EACH ROW EXECUTE FUNCTION careops_audit_immutable()")

    @contextmanager
    def write(self):
        with self.engine.connect() as c:
            try:
                if self.sqlite:
                    c.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    c.begin()
                    c.execute(select(locks.c.id).where(locks.c.id == 1).with_for_update())
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    @contextmanager
    def read(self):
        with self.engine.connect() as c:
            yield c
