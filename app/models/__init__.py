"""SQLAlchemy models, re-exported for convenience.

Models are split across modules by domain (`user`, `content`, `committee`,
`conference`, `audit`, …) so each file stays small and a contributor can
find the relevant chunk quickly. This package-level module re-exports the
common ones for `from app.models import db, User, ...` style imports.
"""
from ..extensions import db

from .user import User, Role, RolePermission, ROLE_NAMES, BUILT_IN_PERMISSIONS
from .otp import OTPCode
from .content import (
    SiteSettings, Page, NavItem, FooterColumn, FooterLink,
    get_site_settings,
)
from .committee import CommitteeMember
from .conference import Conference, PriceTier
from .sponsor import Sponsor, SponsorTier
from .announcement import Announcement
from .registration import Registration
from .abstract import Abstract
from .organising_committee import OrganisingCommitteeMember
from .past_board import PastBoard, PastBoardMember
from .audit import AuditLog

__all__ = [
    "db",
    "User", "Role", "RolePermission", "ROLE_NAMES", "BUILT_IN_PERMISSIONS",
    "OTPCode",
    "SiteSettings", "Page", "NavItem", "FooterColumn", "FooterLink",
    "get_site_settings",
    "CommitteeMember",
    "Conference", "PriceTier",
    "Sponsor", "SponsorTier",
    "Announcement",
    "Registration",
    "Abstract",
    "OrganisingCommitteeMember",
    "PastBoard", "PastBoardMember",
    "AuditLog",
]
