"""Database models for Aixis AI audit platform."""

# User models must be imported first (referenced by ForeignKey in other models)
from .user import Organization, User, AuditReportRecord

# Tool catalog
from .tool import ToolCategory, Tool, ToolTargetConfig

# Audit sessions and test results
from .audit import AuditSession, DBTestCase, DBTestResult

# Scoring
from .score import (
    AxisScoreRecord,
    ToolPublishedScore,
    ScoreHistory,
    ManualChecklistRecord,
)

# Comparison
from .comparison import ComparisonGroup, ComparisonMember, ComparisonNormalizedScore

# Preset
from .preset import AuditPreset

# API Keys
from .api_key import ApiKey

# Webhook
from .webhook import WebhookSubscription, WebhookDelivery

# Notification
from .notification import Notification, NotificationPreference

# Schedule
from .schedule import AuditSchedule

# Vendor
from .vendor import VendorProfile, ToolSubmission, ScoreDispute

# Benchmark
from .benchmark import BenchmarkSuite, BenchmarkTestCase, BenchmarkRun, LeaderboardEntry

__all__ = [
    # user
    "Organization",
    "User",
    "AuditReportRecord",
    # tool
    "ToolCategory",
    "Tool",
    "ToolTargetConfig",
    # audit
    "AuditSession",
    "DBTestCase",
    "DBTestResult",
    # score
    "AxisScoreRecord",
    "ToolPublishedScore",
    "ScoreHistory",
    "ManualChecklistRecord",
    # comparison
    "ComparisonGroup",
    "ComparisonMember",
    "ComparisonNormalizedScore",
    # preset
    "AuditPreset",
    # api key
    "ApiKey",
    # webhook
    "WebhookSubscription",
    "WebhookDelivery",
    # notification
    "Notification",
    "NotificationPreference",
    # schedule
    "AuditSchedule",
    # vendor
    "VendorProfile",
    "ToolSubmission",
    "ScoreDispute",
    # benchmark
    "BenchmarkSuite",
    "BenchmarkTestCase",
    "BenchmarkRun",
    "LeaderboardEntry",
]
