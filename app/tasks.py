import io
import logging
from datetime import datetime
import pandas as pd
from celery import Celery
from dateutil import parser as date_parser
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine, Base
from app.models import Job, Transaction, JobSummary
from app.llm import batch_classify_transactions, generate_spending_summary

# Setup logging
logger = logging.getLogger("pipeline.tasks")

# Initialize Celery
celery_app = Celery("tasks", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

# Create tables if they do not exist
# Normally, we'd use Alembic, but since it's a Docker environment
# auto-creating tables on startup is robust and ensures zero manual setup.
Base.metadata.create_base_dict = {} # placeholder
Base.metadata.create_all(bind=engine)

def parse_date_safe(date_str: str) -> str:
    """
    Safely parse date from DD-MM-YYYY, YYYY/MM/DD, or other mixed formats and return ISO format YYYY-MM-DD.
    """
    if not date_str or pd.isna(date_str) or str(date_str).strip() == "":
        return datetime.utcnow().strftime("%Y-%m-%d")
    
    clean_str = str(date_str).strip()
    try:
        # date_parser.parse is very smart and handles DD-MM-YYYY and YYYY/MM/DD
        # We specify dayfirst=True in case DD-MM-YYYY format is used
        parsed_dt = date_parser.parse(clean_str, dayfirst=True)
        return parsed_dt.strftime("%Y-%m-%d")
    except Exception as e:
        logger.warning(f"Failed to parse date '{date_str}': {e}. Using raw or fallback.")
        return clean_str

def parse_amount_safe(amount_val) -> float:
    """
    Strip currency symbols (like $) and commas, and return float.
    """
    if pd.isna(amount_val):
        return 0.0
    
    val_str = str(amount_val).strip()
    # Strip $, commas, and whitespace
    clean_str = val_str.replace("$", "").replace(",", "").strip()
    try:
        return float(clean_str)
    except ValueError:
        logger.warning(f"Failed to parse amount '{amount_val}'. Defaulting to 0.0")
        return 0.0

@celery_app.task(name="app.tasks.process_transaction_job")
def process_transaction_job(job_id: str, csv_content: str):
    """
    Asynchronous transaction processing pipeline.
    """
    logger.info(f"Starting processing job {job_id}")
    db: Session = SessionLocal()
    
    # 1. Fetch the Job record and set to processing
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        logger.error(f"Job {job_id} not found in database.")
        db.close()
        return
        
    job.status = "processing"
    db.commit()

    try:
        # Load CSV using pandas
        # Convert csv_content string to a file-like object
        df = pd.read_csv(io.StringIO(csv_content))
        
        # Save raw row count
        row_count_raw = len(df)
        job.row_count_raw = row_count_raw
        db.commit()

        # Step a) Data Cleaning
        # Rename columns to ensure lowercase and no whitespace
        df.columns = [col.strip().lower() for col in df.columns]
        
        # Check that required columns exist
        required_cols = ['date', 'merchant', 'amount', 'currency', 'status', 'account_id']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Missing required CSV column: {col}")

        # Normalise dates to ISO 8601 (YYYY-MM-DD)
        df['date'] = df['date'].apply(parse_date_safe)

        # Strip currency symbols and parse amounts
        df['amount'] = df['amount'].apply(parse_amount_safe)

        # Uppercase status values
        df['status'] = df['status'].apply(lambda x: str(x).strip().upper() if not pd.isna(x) else "PENDING")

        # Fill missing categories with 'Uncategorised'
        if 'category' not in df.columns:
            df['category'] = 'Uncategorised'
        else:
            df['category'] = df['category'].apply(lambda x: str(x).strip() if (not pd.isna(x) and str(x).strip() != "") else 'Uncategorised')

        # Normalise currency case (INR or USD)
        df['currency'] = df['currency'].apply(lambda x: str(x).strip().upper() if not pd.isna(x) else "INR")

        # Set default values for account_id and merchant
        df['account_id'] = df['account_id'].apply(lambda x: str(x).strip() if not pd.isna(x) else "UNKNOWN")
        df['merchant'] = df['merchant'].apply(lambda x: str(x).strip() if not pd.isna(x) else "Unknown")
        
        # Handle 'notes' column
        if 'notes' not in df.columns:
            df['notes'] = ''
        else:
            df['notes'] = df['notes'].apply(lambda x: str(x).strip() if not pd.isna(x) else '')

        # Remove exact duplicate rows
        # Keep track of duplicates
        df_cleaned = df.drop_duplicates().copy()
        row_count_clean = len(df_cleaned)
        job.row_count_clean = row_count_clean
        db.commit()

        # Step b) Anomaly Detection
        # 1. Flag amounts exceeding 3x the account's median spend
        # Calculate median per account
        account_medians = df_cleaned.groupby('account_id')['amount'].median().to_dict()
        
        # 2. Flag rows where currency is USD but merchant is Swiggy, Ola, or IRCTC
        domestic_brands = {'swiggy', 'ola', 'irctc'}
        
        # Initialize anomaly columns
        df_cleaned['is_anomaly'] = False
        df_cleaned['anomaly_reason'] = ""
        
        for idx, row in df_cleaned.iterrows():
            reasons = []
            
            # Check 1: 3x account median
            acc_id = row['account_id']
            median = account_medians.get(acc_id, 0.0)
            if median > 0 and row['amount'] > 3 * median:
                reasons.append(f"Amount {row['amount']} exceeds 3x account median of {median}")
                
            # Check 2: Domestic brand in USD
            merchant_lower = row['merchant'].lower()
            if row['currency'] == 'USD' and any(brand in merchant_lower for brand in domestic_brands):
                reasons.append(f"Domestic merchant '{row['merchant']}' billed in USD")
                
            if reasons:
                df_cleaned.at[idx, 'is_anomaly'] = True
                df_cleaned.at[idx, 'anomaly_reason'] = " & ".join(reasons)

        # Convert cleaned DataFrame to a list of dicts for DB inserts
        records = df_cleaned.to_dict(orient='records')
        
        # Step c) LLM Classification
        # Filter transactions without a category (Uncategorised)
        uncategorised_records = [
            {"id": i, "merchant": r['merchant'], "amount": r['amount'], "currency": r['currency'], "notes": r['notes']}
            for i, r in enumerate(records)
            if r['category'] == 'Uncategorised'
        ]
        
        # Call LLM in batch if there are uncategorised records
        classification_results = []
        if uncategorised_records:
            logger.info(f"Sending {len(uncategorised_records)} transactions to Gemini for batch classification.")
            classification_results = batch_classify_transactions(uncategorised_records)
            
            # Map classifications back to records
            class_map = {res['id']: res for res in classification_results}
            for i, r in enumerate(records):
                if i in class_map:
                    res = class_map[i]
                    r['llm_category'] = res['category']
                    r['llm_raw_response'] = res['llm_raw_response']
                    r['llm_failed'] = res['llm_failed']
                    # Overwrite category if classification succeeded
                    if not res['llm_failed'] and res['category'] != "Uncategorised":
                        r['category'] = res['category']
                else:
                    r['llm_category'] = None
                    r['llm_raw_response'] = None
                    r['llm_failed'] = False
        else:
            for r in records:
                r['llm_category'] = None
                r['llm_raw_response'] = None
                r['llm_failed'] = False

        # Create Transaction db instances
        transactions_to_save = []
        for r in records:
            tx = Transaction(
                job_id=job_id,
                txn_id=r.get('txn_id') if not pd.isna(r.get('txn_id')) else None,
                date=r['date'],
                merchant=r['merchant'],
                amount=r['amount'],
                currency=r['currency'],
                status=r['status'],
                category=r['category'],
                account_id=r['account_id'],
                notes=r['notes'],
                is_anomaly=r['is_anomaly'],
                anomaly_reason=r['anomaly_reason'] if r['anomaly_reason'] else None,
                llm_category=r['llm_category'],
                llm_raw_response=r['llm_raw_response'],
                llm_failed=r['llm_failed']
            )
            transactions_to_save.append(tx)
            
        # Bulk save transactions
        db.add_all(transactions_to_save)
        db.flush() # Flushes transactions to DB to get auto IDs if needed (though not required yet)

        # Step d) LLM Narrative Summary
        # Compute aggregates
        # Total spends (only for non-failed transactions or all? Let's do all cleaned transactions)
        total_spend_inr = df_cleaned[df_cleaned['currency'] == 'INR']['amount'].sum()
        total_spend_usd = df_cleaned[df_cleaned['currency'] == 'USD']['amount'].sum()
        
        # Top 3 merchants by spend/count
        merchant_stats = df_cleaned.groupby('merchant').agg(
            spend=('amount', 'sum'),
            count=('amount', 'count')
        ).reset_index()
        
        # Sort by total spend descending
        top_merchants_df = merchant_stats.sort_values(by='spend', ascending=False).head(3)
        top_merchants = top_merchants_df.to_dict(orient='records')
        
        anomaly_count = int(df_cleaned['is_anomaly'].sum())
        
        # Send a summary representation of data to Gemini
        transactions_summary_list = []
        for tx in transactions_to_save[:20]: # send first 20 transactions for narrative context
            transactions_summary_list.append({
                "merchant": tx.merchant,
                "amount": tx.amount,
                "currency": tx.currency,
                "category": tx.category,
                "is_anomaly": tx.is_anomaly,
                "anomaly_reason": tx.anomaly_reason
            })
            
        logger.info("Calling Gemini for narrative spending summary.")
        summary_response = generate_spending_summary(
            total_spend_inr=float(total_spend_inr),
            total_spend_usd=float(total_spend_usd),
            top_merchants=top_merchants,
            anomaly_count=anomaly_count,
            transactions_summary=transactions_summary_list
        )
        
        # Create JobSummary
        job_summary = JobSummary(
            job_id=job_id,
            total_spend_inr=summary_response["total_spend_inr"],
            total_spend_usd=summary_response["total_spend_usd"],
            top_merchants=summary_response["top_merchants"],
            anomaly_count=summary_response["anomaly_count"],
            narrative=summary_response["narrative"],
            risk_level=summary_response["risk_level"]
        )
        db.add(job_summary)

        # Job complete
        job.status = "completed"
        job.completed_at = func.now()
        db.commit()
        logger.info(f"Job {job_id} completed successfully.")

    except Exception as e:
        logger.error(f"Error processing job {job_id}: {e}", exc_info=True)
        db.rollback()
        # Mark job as failed
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = func.now()
            db.commit()
            
    finally:
        db.close()
