from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

# Base Transaction Schema
class TransactionBase(BaseModel):
    txn_id: Optional[str] = None
    date: str
    merchant: str
    amount: float
    currency: str
    status: str
    category: str
    account_id: str
    notes: Optional[str] = None

class TransactionCreate(TransactionBase):
    pass

class TransactionResponse(TransactionBase):
    id: int
    job_id: str
    is_anomaly: bool
    anomaly_reason: Optional[str] = None
    llm_category: Optional[str] = None
    llm_failed: bool

    class Config:
        from_attributes = True


# Job Summary Schema
class JobSummaryBase(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    top_merchants: List[Dict[str, Any]] # e.g. [{"merchant": "Amazon", "spend": 1000.0, "count": 5}]
    anomaly_count: int
    narrative: Optional[str] = None
    risk_level: str # low, medium, high

class JobSummaryResponse(JobSummaryBase):
    id: int
    job_id: str

    class Config:
        from_attributes = True


# Job Status and Info Schemas
class JobBase(BaseModel):
    id: str
    filename: str
    status: str
    row_count_raw: int
    row_count_clean: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

class JobResponse(JobBase):
    class Config:
        from_attributes = True

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    filename: str
    row_count_raw: int
    row_count_clean: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    summary: Optional[JobSummaryBase] = None

    class Config:
        from_attributes = True


# Job Results response schema
class CategorySpend(BaseModel):
    inr: float = 0.0
    usd: float = 0.0

class JobResultsResponse(BaseModel):
    cleaned_transactions: List[TransactionResponse]
    flagged_anomalies: List[TransactionResponse]
    category_spend_breakdown: Dict[str, CategorySpend]
    llm_summary: Optional[JobSummaryBase] = None


# Job Upload Response
class JobUploadResponse(BaseModel):
    job_id: str
    status: str
    message: str
