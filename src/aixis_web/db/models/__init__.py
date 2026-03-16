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

# Industry & Use Case Tags
from .tool_industry import IndustryTag, ToolIndustryMapping, UseCaseTag, ToolUseCaseMapping

# Risk & Governance
from .risk_governance import ToolRiskGovernance, RegulatoryFramework

# Adoption / Benchmark
from .adoption import IndustryAdoptionPattern, AdoptionSurveyResponse

# Rate limiting (DB-backed for multi-worker support)
from .rate_limit import RateLimitEntry

# Token revocation (for logout)
from .revoked_token import RevokedToken

# User sessions (concurrent session tracking)
from .user_session import UserSession

# Audit log (operation tracking)
from .audit_log import AuditLog

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
    # industry & use case
    "IndustryTag",
    "ToolIndustryMapping",
    "UseCaseTag",
    "ToolUseCaseMapping",
    # risk & governance
    "ToolRiskGovernance",
    "RegulatoryFramework",
    # adoption
    "IndustryAdoptionPattern",
    "AdoptionSurveyResponse",
    # rate limiting
    "RateLimitEntry",
    # token revocation
    "RevokedToken",
    # user sessions
    "UserSession",
    # audit log
    "AuditLog",
]
