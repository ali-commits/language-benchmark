from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HashSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    hash_concurrency: Literal[1, 2] = 2

    @field_validator("hash_concurrency", mode="before")
    @classmethod
    def parse_hash_concurrency(cls, value):
        if isinstance(value, str) and value in ("1", "2"):
            return int(value)
        return value


class Settings(HashSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    database_url: str
    jwt_secret: str = Field(min_length=32)
    jwt_ttl_seconds: int = Field(default=3600, ge=600, le=86400)
    workers: Literal[1, 2, 4] = 2
    db_pool_max_size: int | None = Field(default=None, ge=1, le=100)
    db_pool_min_size: int = Field(default=2, ge=1, le=100)
    mode: str = "local"
    run_dir: str = ""
    run_id: str = ""
    expected_gil: bool = True
    expected_jit: bool = False
    thread_limit: int = 16

    @field_validator("workers", mode="before")
    @classmethod
    def parse_workers(cls, value):
        if isinstance(value, str) and value in ("1", "2", "4"):
            return int(value)
        return value

    @field_validator("db_pool_max_size", "db_pool_min_size", mode="before")
    @classmethod
    def parse_pool_size(cls, value):
        if isinstance(value, str):
            if not value.isascii() or not value.isdecimal() or value.startswith("0"):
                raise ValueError("pool sizes must be positive decimal integers")
            return int(value)
        return value

    @model_validator(mode="after")
    def validate_db(self):
        from urllib.parse import parse_qs, urlsplit

        if self.db_pool_min_size > (self.db_pool_max_size or 20 // self.workers):
            raise ValueError("DB_POOL_MIN_SIZE must not exceed DB_POOL_MAX_SIZE")
        if self.thread_limit != 16:
            raise ValueError("fixed thread limit is 16")
        u = urlsplit(self.database_url.replace("postgresql+psycopg://", "postgresql://"))
        if u.scheme != "postgresql":
            raise ValueError("PostgreSQL required")
        q = parse_qs(u.query)
        if self.mode == "aws" and (q.get("sslmode") != ["verify-full"] or not q.get("sslrootcert")):
            raise ValueError("AWS DB requires sslmode=verify-full and sslrootcert")
        return self
