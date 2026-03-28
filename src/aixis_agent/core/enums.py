"""Enumerations for the Aixis AI audit platform."""

from enum import Enum


class TestCategory(str, Enum):
    """Categories of test patterns."""
    # Slide-creation-specific categories
    SLIDE_BASIC = "slide_basic"
    SLIDE_STRUCTURE = "slide_structure"
    SLIDE_JAPANESE = "slide_japanese"
    SLIDE_ACCURACY = "slide_accuracy"
    SLIDE_ADVANCED = "slide_advanced"
    # UI evaluation category
    UI_EVALUATION = "ui_evaluation"
    # Legacy categories (kept for backward compatibility)
    DIALECT = "dialect"
    LONG_INPUT = "long_input"
    CONTRADICTORY = "contradictory"
    AMBIGUOUS = "ambiguous"
    KEIGO_MIXING = "keigo_mixing"
    UNICODE_EDGE = "unicode_edge"
    BUSINESS_JP = "business_jp"
    MULTI_STEP = "multi_step"
    BROKEN_GRAMMAR = "broken_grammar"


class ScoreAxis(str, Enum):
    """Aixis 5-axis scoring model (Aixis Scoring Model).

    Each axis is scored on 0.0-5.0 scale.
    Some axes combine automated + manual evaluation.
    """
    PRACTICALITY = "practicality"           # 実務適性
    COST_PERFORMANCE = "cost_performance"   # 費用対効果
    LOCALIZATION = "localization"           # 日本語能力
    SAFETY = "safety"                       # 信頼性・安全性
    UNIQUENESS = "uniqueness"               # 革新性

    @property
    def name_jp(self) -> str:
        return _AXIS_NAMES_JP[self.value]

    @property
    def name_en(self) -> str:
        return _AXIS_NAMES_EN[self.value]


_AXIS_NAMES_JP = {
    "practicality": "実務適性",
    "cost_performance": "費用対効果",
    "localization": "日本語能力",
    "safety": "信頼性・安全性",
    "uniqueness": "革新性",
}

_AXIS_NAMES_EN = {
    "practicality": "Practicality",
    "cost_performance": "Cost Performance",
    "localization": "Localization",
    "safety": "Safety & Reliability",
    "uniqueness": "Uniqueness",
}


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class OverallGrade(str, Enum):
    """Overall audit grade derived from 0.0-5.0 scale."""
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"

    @classmethod
    def from_score(cls, score: float) -> "OverallGrade":
        """Map 0.0-5.0 overall score to letter grade."""
        if score >= 4.5:
            return cls.S
        if score >= 3.8:
            return cls.A
        if score >= 3.0:
            return cls.B
        if score >= 2.0:
            return cls.C
        return cls.D

    @property
    def label_jp(self) -> str:
        return {
            "S": "最高評価",
            "A": "推奨",
            "B": "標準",
            "C": "要注意",
            "D": "非推奨",
        }[self.value]


class ExecutorType(str, Enum):
    EXTENSION = "extension"
    API = "api"


class ScoreSource(str, Enum):
    """How a score was determined."""
    AUTO = "auto"
    MANUAL = "manual"
    HYBRID = "hybrid"
    LLM = "llm"


class AuditStatus(str, Enum):
    """Audit session lifecycle states."""
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_MANUAL = "awaiting_manual"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserRole(str, Enum):
    """Platform user roles."""
    ADMIN = "admin"
    ANALYST = "analyst"
    CLIENT = "client"
    VIEWER = "viewer"


class SubscriptionTier(str, Enum):
    """Organization subscription levels."""
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class ReportType(str, Enum):
    """Types of generated audit reports."""
    INDIVIDUAL = "individual"
    COMPARISON = "comparison"
    TREND = "trend"
    CERTIFICATION = "certification"
    CATEGORY_RANKING = "category_ranking"
