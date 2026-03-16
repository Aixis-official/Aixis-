"""Subscription service — check subscription status and feature access."""

from dataclasses import dataclass
from datetime import datetime, timezone

from ..db.models.user import User


@dataclass
class SubscriptionInfo:
    """Subscription state for a user."""
    tier: str  # "free" | "trial" | "standard" | "professional" | "enterprise"
    is_active: bool
    is_trial: bool
    days_remaining: int | None  # None if not trial or no end date
    features: set[str]  # Feature flags enabled for this tier


# Feature sets by tier
_TIER_FEATURES: dict[str, set[str]] = {
    "free": {
        "view_tools_summary",
        "view_rankings_summary",
    },
    "trial": {
        "view_tools_summary",
        "view_tools_detail",
        "view_scores",
        "view_rankings_summary",
        "view_rankings_detail",
        "view_comparisons",
        "export_pdf",
    },
    "standard": {
        "view_tools_summary",
        "view_tools_detail",
        "view_scores",
        "view_rankings_summary",
        "view_rankings_detail",
        "view_comparisons",
        "export_pdf",
        "api_access",
    },
    "professional": {
        "view_tools_summary",
        "view_tools_detail",
        "view_scores",
        "view_rankings_summary",
        "view_rankings_detail",
        "view_comparisons",
        "export_pdf",
        "api_access",
        "custom_reports",
        "priority_support",
    },
    "enterprise": {
        "view_tools_summary",
        "view_tools_detail",
        "view_scores",
        "view_rankings_summary",
        "view_rankings_detail",
        "view_comparisons",
        "export_pdf",
        "api_access",
        "custom_reports",
        "priority_support",
        "dedicated_audits",
        "sla",
    },
}

# Admin/auditor/analyst always have full access
_ADMIN_ROLES = frozenset({"admin", "analyst", "auditor"})


def get_subscription_info(user: User | None) -> SubscriptionInfo:
    """Determine subscription state for a user."""
    # Unauthenticated = free tier
    if not user:
        return SubscriptionInfo(
            tier="free",
            is_active=True,
            is_trial=False,
            days_remaining=None,
            features=_TIER_FEATURES["free"],
        )

    # Admin roles get full access regardless of subscription
    if user.role in _ADMIN_ROLES:
        return SubscriptionInfo(
            tier="enterprise",
            is_active=True,
            is_trial=False,
            days_remaining=None,
            features=_TIER_FEATURES["enterprise"],
        )

    tier = getattr(user, "subscription_tier", None) or "trial"
    is_trial = tier == "trial"
    is_active = user.is_active
    days_remaining = None

    if is_trial and hasattr(user, "trial_end") and user.trial_end:
        now = datetime.now(timezone.utc)
        trial_end = user.trial_end
        if trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)
        days_remaining = max(0, (trial_end - now).days)
        if days_remaining <= 0:
            is_active = False

    features = _TIER_FEATURES.get(tier, _TIER_FEATURES["free"])
    if not is_active:
        features = _TIER_FEATURES["free"]

    return SubscriptionInfo(
        tier=tier,
        is_active=is_active,
        is_trial=is_trial,
        days_remaining=days_remaining,
        features=features,
    )


def has_feature(user: User | None, feature: str) -> bool:
    """Check if a user has access to a specific feature."""
    info = get_subscription_info(user)
    return feature in info.features
