"""
WhatsApp Alert Log Model — database table for delivery tracking
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Stores per-message delivery status for:
  - Success/failure audit trail
  - Retry tracking
  - Invalid number detection & auto-disable
  - Daily summary reports
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float, Index
from datetime import datetime
from database import Base


class WhatsAppAlertLog(Base):
    __tablename__ = "whatsapp_alert_logs"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, nullable=True, index=True)  # FK to users.id (soft ref)
    phone          = Column(String(20), nullable=False, index=True)
    language       = Column(String(5),  nullable=False, default="en")
    district       = Column(String(100), nullable=True)
    crop           = Column(String(100), nullable=True)

    # Message content snapshot (first 500 chars for debugging)
    message_preview = Column(String(500), nullable=True)
    message_id      = Column(String(100), nullable=True)  # Meta message ID on success

    # Delivery
    success       = Column(Boolean, nullable=False, default=False)
    error_message = Column(Text, nullable=True)
    attempt_count = Column(Integer, default=1)

    # Data used
    price_used    = Column(Float, nullable=True)
    weather_used  = Column(String(200), nullable=True)

    # Job run this log belongs to
    job_run_id    = Column(String(50), nullable=True, index=True)

    sent_at       = Column(DateTime, default=datetime.utcnow, index=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wa_log_sent_at_success", "sent_at", "success"),
        Index("ix_wa_log_job_run", "job_run_id", "success"),
    )


class WhatsAppJobRun(Base):
    """
    Summary record per daily cron execution.
    One row per job run — useful for dashboard monitoring.
    """
    __tablename__ = "whatsapp_job_runs"

    id              = Column(Integer, primary_key=True, index=True)
    run_id          = Column(String(50), unique=True, nullable=False, index=True)
    triggered_by    = Column(String(50), default="cron")   # "cron" | "manual"

    total_farmers   = Column(Integer, default=0)
    sent_success    = Column(Integer, default=0)
    sent_failure    = Column(Integer, default=0)
    invalid_numbers = Column(Integer, default=0)
    skipped         = Column(Integer, default=0)

    duration_seconds = Column(Float, nullable=True)
    status           = Column(String(20), default="running")  # running | completed | failed

    started_at    = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at  = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow)


class WhatsAppOptOut(Base):
    """
    Tracks farmers who replied STOP — do not send them alerts.
    Also auto-populated when Meta returns invalid number error.
    """
    __tablename__ = "whatsapp_opt_outs"

    id         = Column(Integer, primary_key=True, index=True)
    phone      = Column(String(20), unique=True, nullable=False, index=True)
    reason     = Column(String(50), default="user_request")  # user_request | invalid_number
    opted_out_at = Column(DateTime, default=datetime.utcnow)
    created_at   = Column(DateTime, default=datetime.utcnow)
