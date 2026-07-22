"""SQLAlchemy models, re-exported for convenience.

Models are split across modules by domain (`user`, `content`, `committee`,
`conference`, `audit`, …) so each file stays small and a contributor can
find the relevant chunk quickly. This package-level module re-exports the
common ones for `from app.models import db, User, ...` style imports.
"""
from ..extensions import db

from .user import User, Role, RolePermission, ROLE_NAMES, BUILT_IN_PERMISSIONS, IMPLICIT_PERMISSIONS
from .otp import OTPCode
from .content import (
    SiteSettings, Page, NavItem, FooterColumn, FooterLink,
    get_site_settings,
    PaymentGatewayConfig, DocumentTemplate, FinancialIdentity,
    get_payment_gateway_config, get_active_payment_gateway,
    get_document_template, get_financial_identity,
)
from .committee import CommitteeMember
from .conference import Conference, PriceTier
from .sponsor import Sponsor, SponsorTier
from .announcement import Announcement
from .registration import Registration
from .payment_event import PaymentEvent, record_payment_event
from .issued_document import IssuedDocument
from .abstract import Abstract, SPEAKER_STATUSES, SPEAKER_STATUS_ORDER, ALL_STATUSES
from .organising_committee import OrganisingCommitteeMember
from .past_board import PastBoard, PastBoardMember
from .form_template import FormTemplate
from .sub_event import SubEvent
from .audit import AuditLog
from .reviews import ConferenceReviewer, ReviewAssignment

__all__ = [
    "db",
    "User", "Role", "RolePermission", "ROLE_NAMES", "BUILT_IN_PERMISSIONS",
    "IMPLICIT_PERMISSIONS",
    "OTPCode",
    "SiteSettings", "Page", "NavItem", "FooterColumn", "FooterLink",
    "get_site_settings",
    "PaymentGatewayConfig", "DocumentTemplate", "FinancialIdentity",
    "get_payment_gateway_config", "get_active_payment_gateway",
    "get_document_template", "get_financial_identity",
    "CommitteeMember",
    "Conference", "PriceTier",
    "Sponsor", "SponsorTier",
    "Announcement",
    "Registration",
    "PaymentEvent", "record_payment_event",
    "IssuedDocument",
    "Abstract", "SPEAKER_STATUSES", "SPEAKER_STATUS_ORDER", "ALL_STATUSES",
    "OrganisingCommitteeMember",
    "PastBoard", "PastBoardMember",
    "FormTemplate",
    "SubEvent",
    "AuditLog",
]
