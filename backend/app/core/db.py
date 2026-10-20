from collections.abc import Iterator

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _wal(conn, _):  # concurrent job writers + request readers
    conn.execute("PRAGMA journal_mode=WAL")


def init_db(e=engine) -> None:
    from app.core import models  # noqa: F401  (register tables)
    SQLModel.metadata.create_all(e)
    insp = inspect(e)
    with e.begin() as c:  # ponytail: adds missing columns only; renames/drops need a real migration
        for table in SQLModel.metadata.sorted_tables:
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    c.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(e.dialect)}'))


def get_session() -> Iterator[Session]:
    with Session(engine) as s:
        yield s
