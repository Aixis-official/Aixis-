"""Access tier service — determines what a user can see.

The 2026-04-15 free-registration pivot moved the platform from a paid
subscription model to a two-tier access model:

* ``anonymous`` — not logged in. Sees summary-level information on every
  tool (name, vendor, category, short description, overall grade) so the
  database remains crawlable by search engines and useful to casual
  visitors. The /compare endpoint is capped at two tools. No detail pages
  or PDF export.
* ``registered`` — logged in with an active account and verified email.
  Sees every audit output: 5-axis breakdown, score history, positioning,
  pros/cons, risk/governance, detailed analysis report, unlimited
  comparisons, and PDF export.

Admin, analyst, and auditor roles always resolve to ``registered`` access
regardless of any other state — they are staff, not customers.

The ``subscription_tier`` column on ``User`` is intentionally preserved so a
future optional paid tier can be layered on without another migration.
"""

from dataclasses import dataclass

from ..db.models.user import User

TIER_ANONYMOUS = "anonymous"
TIER_REGISTERED = "registered"

_REGISTERED_FEATURES: frozenset[str] = frozenset(
    {
        "view_tools_summary",
        "view_tools_detail",
        "view_scores",
        "view_score_history",
        "view_positioning",
        "view_pros_cons",
        "view_risk_governance",
        "view_analysis_report",
        "view_rankings_summary",
        "view_rankings_detail",
        "view_comparisons",
        "view_comparisons_unlimited",
        "export_pdf",
        "api_access",
    }
)

_ANONYMOUS_FEATURES: frozenset[str] = frozenset(
    {
        "view_tools_summary",
        "view_rankings_summary",
        "view_comparisons",  # capped to 2 tools — see max_comparison_tools()
    }
)

# Internal roles always get full access, bypassing any account-state checks.
_STAFF_ROLES: frozenset[str] = frozenset({"admin", "analyst", "auditor"})

# Anonymous users may compare at most this many tools at once.
ANONYMOUS_COMPARE_LIMIT = 2
REGISTERED_COMPARE_LIMIT = 10


@dataclass
class SubscriptionInfo:
    """Access state for a user. Used by templates and API dependencies.

    The legacy ``is_trial`` / ``days_remaining`` fields are kept so
    existing templates (notably ``mypage.html``) continue to render; they
    are always ``False`` / ``None`` in the free-registration model.
    """

    tier: str  # "anonymous" | "registered"
    is_active: bool
    is_registered: bool
    is_staff: bool
    features: frozenset[str]
    is_trial: bool = False
    days_remaining: int | None = None


def get_subscription_info(user: User | None) -> SubscriptionInfo:
    """Return the access-tier summary for ``user`` (or anonymous if ``None``).

    A deactivated user degrades to anonymous access rather than an error
    — this avoids 500s on pages that read ``ctx["subscription"]`` and
    keeps the fall-through behaviour consistent for templates.
    """
    if user is None:
        return SubscriptionInfo(
            tier=TIER_ANONYMOUS,
            is_active=True,
            is_registered=False,
            is_staff=False,
            features=_ANONYMOUS_FEATURES,
        )

    if not user.is_active:
        return SubscriptionInfo(
            tier=TIER_ANONYMOUS,
            is_active=False,
            is_registered=False,
            is_staff=False,
            features=_ANONYMOUS_FEATURES,
        )

    is_staff = (user.role or "").lower() in _STAFF_ROLES
    return SubscriptionInfo(
        tier=TIER_REGISTERED,
        is_active=True,
        is_registered=True,
        is_staff=is_staff,
        features=_REGISTERED_FEATURES,
    )


def has_feature(user: User | None, feature: str) -> bool:
    """Return whether ``user`` has access to the named feature flag."""
    return feature in get_subscription_info(user).features


def can_view_detail(user: User | None) -> bool:
    """Shortcut: does this user see detailed tool information?"""
    return has_feature(user, "view_tools_detail")


def max_comparison_tools(user: User | None) -> int:
    """How many tools can this user include in a single comparison?"""
    if has_feature(user, "view_comparisons_unlimited"):
        return REGISTERED_COMPARE_LIMIT
    return ANONYMOUS_COMPARE_LIMIT
