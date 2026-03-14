"""Benchmark and leaderboard models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)

from ..base import Base


def new_uuid():
    return str(uuid.uuid4())


class BenchmarkSuite(Base):
    __tablename__ = "benchmark_suites"

    id = Column(String(36), primary_key=True, default=new_uuid)
    slug = Column(String(100), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    name_jp = Column(String(200), nullable=False)
    description = Column(Text, default="")
    description_jp = Column(Text, default="")
    version = Column(String(20), default="v1.0")
    category_id = Column(String(36), ForeignKey("tool_categories.id"), nullable=True)
    test_case_count = Column(Integer, default=0)
    is_published = Column(Boolean, default=False)
    published_at = Column(DateTime, nullable=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BenchmarkTestCase(Base):
    __tablename__ = "benchmark_test_cases"

    id = Column(String(36), primary_key=True, default=new_uuid)
    suite_id = Column(String(36), ForeignKey("benchmark_suites.id"), nullable=False)
    category = Column(String(50), nullable=False)
    prompt = Column(Text, nullable=False)
    expected_behaviors = Column(JSON, default=list)
    failure_indicators = Column(JSON, default=list)
    weight = Column(Float, default=1.0)
    tags = Column(JSON, default=list)
    sort_order = Column(Integer, default=0)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    suite_id = Column(String(36), ForeignKey("benchmark_suites.id"), nullable=False)
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    session_id = Column(String(36), ForeignKey("audit_sessions.id"), nullable=True)
    suite_version = Column(String(20), default="v1.0")
    total_cases = Column(Integer, default=0)
    passed_cases = Column(Integer, default=0)
    score = Column(Float, default=0.0)  # 0-100 percentage
    axis_scores = Column(JSON, default=dict)
    details = Column(JSON, default=dict)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class LeaderboardEntry(Base):
    __tablename__ = "leaderboard_entries"

    id = Column(String(36), primary_key=True, default=new_uuid)
    suite_id = Column(
        String(36), ForeignKey("benchmark_suites.id"), nullable=False, index=True
    )
    tool_id = Column(String(36), ForeignKey("tools.id"), nullable=False)
    best_run_id = Column(String(36), ForeignKey("benchmark_runs.id"), nullable=True)
    best_score = Column(Float, default=0.0)
    rank = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)
