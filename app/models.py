from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, index=True) # UUID string
    filename = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False) # pending, processing, completed, failed
    row_count_raw = Column(Integer, default=0, nullable=False)
    row_count_clean = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationships
    transactions = relationship("Transaction", back_populates="job", cascade="all, delete-orphan")
    summary = relationship("JobSummary", uselist=False, back_populates="job", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    
    # Original / Cleaned data fields
    txn_id = Column(String, nullable=True) # PDF says "some rows have this blank"
    date = Column(String, nullable=False) # normalised date format YYYY-MM-DD
    merchant = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False) # INR or USD
    status = Column(String, nullable=False) # SUCCESS, FAILED, PENDING
    category = Column(String, nullable=False) # filled with Uncategorised if empty
    account_id = Column(String, nullable=False)
    notes = Column(String, nullable=True)

    # Anomaly Detection fields
    is_anomaly = Column(Boolean, default=False, nullable=False)
    anomaly_reason = Column(String, nullable=True)

    # LLM Enrichments
    llm_category = Column(String, nullable=True)
    llm_raw_response = Column(Text, nullable=True)
    llm_failed = Column(Boolean, default=False, nullable=False)

    # Relationship
    job = relationship("Job", back_populates="transactions")


class JobSummary(Base):
    __tablename__ = "job_summaries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    total_spend_inr = Column(Float, default=0.0, nullable=False)
    total_spend_usd = Column(Float, default=0.0, nullable=False)
    top_merchants = Column(JSON, nullable=False) # JSON dict of {merchant: count/spend} or array
    anomaly_count = Column(Integer, default=0, nullable=False)
    narrative = Column(Text, nullable=True)
    risk_level = Column(String, nullable=False) # low/medium/high

    # Relationship
    job = relationship("Job", back_populates="summary")
