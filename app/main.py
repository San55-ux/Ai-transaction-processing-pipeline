import os
import uuid
import logging
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.database import get_db, engine, Base
from app.models import Job, Transaction, JobSummary
from app.schemas import (
    JobUploadResponse,
    JobStatusResponse,
    JobResultsResponse,
    JobResponse,
    TransactionResponse,
    JobSummaryBase,
    CategorySpend
)
from app.tasks import process_transaction_job

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pipeline.main")

# Initialize DB tables (redundant but safe)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI-Powered Transaction Processing Pipeline API",
    description="Backend API for uploading financial transactions, running asynchronous cleaning/enrichments, and polling reports.",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints

@app.post("/jobs/upload", response_model=JobUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Accept a CSV file upload. Validate it, create a Job record in the database with
    status=pending, enqueue the processing task, and return the job_id immediately.
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV files are accepted."
        )
        
    try:
        # Read the file content
        content_bytes = await file.read()
        csv_content = content_bytes.decode("utf-8")
        
        # Basic validation: check if empty
        if not csv_content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded CSV file is empty."
            )
            
        # Create unique Job ID
        job_id = str(uuid.uuid4())
        
        # Save Job record in Database
        db_job = Job(
            id=job_id,
            filename=file.filename,
            status="pending",
            row_count_raw=0,
            row_count_clean=0
        )
        db.add(db_job)
        db.commit()
        
        # Enqueue processing task via Celery
        process_transaction_job.delay(job_id, csv_content)
        logger.info(f"Enqueued Celery task for job {job_id}")
        
        return JobUploadResponse(
            job_id=job_id,
            status="pending",
            message="CSV uploaded successfully. Processing enqueued."
        )
        
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Error decoding CSV file. Please ensure it is UTF-8 encoded."
        )
    except Exception as e:
        logger.error(f"Error handling CSV upload: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during file upload: {str(e)}"
        )

@app.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """
    Return the current status of the job: pending, processing, completed, or failed.
    If completed, also include a summary field with high-level stats.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found."
        )
        
    summary_data = None
    if job.status == "completed" and job.summary:
        summary_data = JobSummaryBase(
            total_spend_inr=job.summary.total_spend_inr,
            total_spend_usd=job.summary.total_spend_usd,
            top_merchants=job.summary.top_merchants,
            anomaly_count=job.summary.anomaly_count,
            narrative=job.summary.narrative,
            risk_level=job.summary.risk_level
        )
        
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        filename=job.filename,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        summary=summary_data
    )

@app.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
def get_job_results(job_id: str, db: Session = Depends(get_db)):
    """
    Return the full structured output: cleaned transactions list, flagged anomalies,
    per-category spend breakdown, and the LLM-generated narrative summary.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found."
        )
        
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job {job_id} is in '{job.status}' status. Results are only available for completed jobs."
        )
        
    # Fetch cleaned transactions
    transactions = db.query(Transaction).filter(Transaction.job_id == job_id).all()
    
    # Flagged anomalies
    anomalies = [tx for tx in transactions if tx.is_anomaly]
    
    # Calculate per-category spend breakdown
    # Format: {category: {inr: amount_inr, usd: amount_usd}}
    breakdown = {}
    for tx in transactions:
        cat = tx.category or "Uncategorised"
        if cat not in breakdown:
            breakdown[cat] = CategorySpend()
            
        if tx.currency == "INR":
            breakdown[cat].inr += tx.amount
        elif tx.currency == "USD":
            breakdown[cat].usd += tx.amount

    # Fetch JobSummary
    summary_data = None
    if job.summary:
        summary_data = JobSummaryBase(
            total_spend_inr=job.summary.total_spend_inr,
            total_spend_usd=job.summary.total_spend_usd,
            top_merchants=job.summary.top_merchants,
            anomaly_count=job.summary.anomaly_count,
            narrative=job.summary.narrative,
            risk_level=job.summary.risk_level
        )
        
    return JobResultsResponse(
        cleaned_transactions=[TransactionResponse.model_validate(t) for t in transactions],
        flagged_anomalies=[TransactionResponse.model_validate(t) for t in anomalies],
        category_spend_breakdown=breakdown,
        llm_summary=summary_data
    )

@app.get("/jobs", response_model=List[JobResponse])
def list_jobs(
    status: Optional[str] = Query(None, description="Filter jobs by status (pending, processing, completed, failed)"),
    db: Session = Depends(get_db)
):
    """
    List all jobs with their status, filename, row count, and created_at timestamp.
    Supports filtering via ?status= query parameter.
    """
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status.lower())
        
    # Order by newest first
    jobs = query.order_by(Job.created_at.desc()).all()
    return [JobResponse.model_validate(j) for j in jobs]


# Mount frontend static files
# Place after API endpoints so API routes are matched first.
# Create the directory if not exists
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def read_root():
    """
    Serve index.html at root url.
    """
    index_path = "app/static/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "AI Transaction Processing Pipeline API is running. Dashboard files are being set up."}
