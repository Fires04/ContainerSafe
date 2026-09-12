from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .migrations import run_startup_migrations
from .models import Base

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
config.RESTORE_TMP_DIR.mkdir(parents=True, exist_ok=True)
config.INCOMING_DIR.mkdir(parents=True, exist_ok=True)

# check_same_thread=False: APScheduler's background thread and FastAPI's
# request-handling both open sessions from this one engine; each session
# still gets its own connection, SQLAlchemy just refuses same-thread
# reuse checks that don't apply to how we use it (one Session per
# call, never shared across threads).
engine = create_engine(f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    run_startup_migrations(engine)


def ensure_default_local_target() -> None:
    """The local ./backup folder should always be usable without the user
    ever having to add it by hand — seed a "Local" storage target
    (root of /app/backup) whenever no local-type target exists yet. Runs
    on every startup (not just first-ever init) so it also comes back if
    every local target was ever deleted, rather than only seeding once."""
    from .models import StorageTarget

    with get_session() as session:
        if session.query(StorageTarget).filter_by(type="local").first() is None:
            session.add(StorageTarget(name="Local", type="local", config_json={"path": ""}))
            session.commit()


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
