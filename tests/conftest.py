import os

# Point every test run at throwaway local connection strings before any
# app module (which reads settings at import time) gets imported. Tests
# never actually open these connections unless a test explicitly opts in.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://lmx:test@localhost:5432/lmx_os_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("ENVIRONMENT", "test")
