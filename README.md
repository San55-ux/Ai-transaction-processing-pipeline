# AI-Powered Transaction Processing Pipeline

This repository contains the complete implementation of the AI-Powered Transaction Processing Pipeline. The application cleans and normalises dirty financial transactions, detects statistical outliers and fraud-risk anomalies, classifies uncategorised transactions using the Gemini 1.5 Flash LLM, and generates an executive summary report, all visible through an interactive Single Page Application (SPA) dashboard.

The project is built using **FastAPI**, **Celery**, **Redis**, **PostgreSQL**, and **Docker / Docker Compose**.

---

## Architecture & Data Flow

```
                     +---------------------------------------+
                     |            User / Browser             |
                     +---------------------------------------+
                       | Upload CSV                    ^ Poll status /
                       |                               | Get analysis
                       v                               |
            +--------------------+           +--------------------+
            | FastAPI API Server |           | FastAPI Static     |
            |   (Port 8000)      |           | Dashboard Frontend |
            +--------------------+           +--------------------+
               |              |                         ^
        Create |       Enqueue|                         | Reads
        Record |       Task   |                         | Data
               v              v                         |
      +------------+   +-------------+           +--------------+
      | PostgreSQL |   | Redis Broker| <-------> | Celery Task  |
      | Database   |   | (Port 6379) | Dequeue   | Worker       |
      +------------+   +-------------+           +--------------+
             ^                                          |
             |                                          | 1. Clean data
             |                                          | 2. Detect anomalies
             |                                          | 3. Batch LLM Classify
             |                                          | 4. Generate LLM summary
             |                                          v
             +---------------------------------- [ Gemini 1.5 API ]
                                                   (Free Tier)
```

### Request Lifecycle
1. **CSV Upload**: The user uploads `transactions.csv` via `POST /jobs/upload` or the UI.
2. **Enqueueing**: The API validates the file format, creates a `Job` record in the database with status `pending`, enqueues a Celery processing task to Redis, and returns the `job_id` immediately.
3. **Asynchronous Execution**:
   - The Celery worker dequeues the job and updates the job status to `processing`.
   - **Data Cleaning**: Normalises date formats (supporting `DD-MM-YYYY`, `YYYY/MM/DD`, etc.) to ISO 8601 (`YYYY-MM-DD`), strips currency symbols from amount fields, standardizes transaction statuses to uppercase, removes duplicate rows, and fills blank categories with `'Uncategorised'`.
   - **Statistical Anomaly Detection**:
     - Calculates the median spend for each account. Flags any transaction exceeding 3x the median as an outlier.
     - Flags any USD currency transaction mapped to a domestic-only merchant (Swiggy, Ola, IRCTC).
   - **LLM Batch Classification**: Groups all transactions marked as 'Uncategorised' and sends them to **Gemini 1.5 Flash** in a single structured JSON batch call to map transactions to one of: *Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, or Other*.
   - **LLM Summary Narrative**: Computes total spends by currency, top merchants, and anomalies count. Sends aggregates to Gemini to write a 2-3 sentence executive spending narrative and assign a risk level (`low`, `medium`, `high`).
4. **Data Persistence**: Enriched transactions and the narrative report summary are persisted to the database. The job state is updated to `completed`.
5. **Polling Results**: The UI polls `/jobs/{job_id}/status` until it is marked `completed`, then fetches full reports from `/jobs/{job_id}/results` to render interactive data tables and spending breakdown charts.

---

## Technical Review: System Design, Bottlenecks & Scale

*Below is the technical breakdown to support your **3-Minute Technical Video Review** requirements:*

### 1. The Blueprint & "Why"
- **FastAPI**: Chosen for its high-performance asynchronous execution, automatic validation schemas via Pydantic, and automatic Swagger docs.
- **Celery + Redis**: Celery is a robust distributed queue system that allows us to offload heavy processing steps (like calling external LLM APIs) off the web thread. Redis acts as a fast, in-memory transport broker.
- **PostgreSQL**: Relational storage chosen to ensure transaction integrity (ACID properties), clean schema enforcement, and relational query support for complex financial data audits.
- **Folder Structure**: Clean separation of database schemas (`models.py`), request/response validation (`schemas.py`), external API calls (`llm.py`), processing jobs (`tasks.py`), and REST routing (`main.py`).

### 2. The Breaking Point (100x Scale)
If traffic scales by 100x tomorrow, the system will face three critical bottlenecks:
1. **Database Connections & Writing**: Synchronous database transactions inside Celery could exhaust the PostgreSQL connection pool. Under massive writes, the database disk I/O would saturate.
2. **Worker Concurrency**: If 100x CSVs (each having thousands of rows) are uploaded, Celery workers will run out of memory or experience long queue delays, since batch processing happens in memory.
3. **LLM Rate Limits**: External API calls to Gemini 1.5 Flash will hit Rate Limits (RPM/TPM constraints) leading to multiple HTTP 429 exceptions.

### 3. The Next Iteration (Enterprise Architecture)
To scale this codebase for enterprise production:
- **Connection Pooling & Read Replicas**: Introduce `pgBouncer` to pool database connections, and spin up read replicas to distribute reading traffic (e.g. results polling) away from the write master database.
- **Message Broker & Worker Scaling**: Migrate Redis to **Apache Kafka** or **AWS SQS** for durable partitioned queues. Scale Celery workers horizontally inside a Kubernetes cluster (using KEDA based on queue depth).
- **Batching & Stream Processing**: Instead of parsing full CSVs in memory, stream rows using **Apache Flink** or **Celery Chunking**, storing intermediate chunks.
- **Rate-Limit Gateways & Caching**: Put LLM calls behind a centralized proxy queue implementing token bucket rate-limiting. Cache common classification queries (e.g. Swiggy -> Food) in Redis to avoid hitting the LLM for identical transactions.

---

## Database Schema

```
  +------------------+          +------------------------+
  |       jobs       |          |      transactions      |
  +------------------+          +------------------------+
  | id (PK)          | <------+ | id (PK)                |
  | filename         |        | | job_id (FK)            |
  | status           |        | | txn_id                 |
  | row_count_raw    |        | | date (YYYY-MM-DD)      |
  | row_count_clean  |        | | merchant               |
  | created_at       |        | | amount                 |
  | completed_at     |        | | currency               |
  | error_message    |        | | status                 |
  +------------------+        | | category               |
           |                  | | account_id             |
           | 1:1              | | notes                  |
           v                  | | is_anomaly             |
  +------------------+        | | anomaly_reason         |
  |  job_summaries   |        | | llm_category           |
  +------------------+        | | llm_raw_response       |
  | id (PK)          |        | | llm_failed             |
  | job_id (FK, UK)  |        +------------------------+
  | total_spend_inr  |
  | total_spend_usd  |
  | top_merchants    |
  | anomaly_count    |
  | narrative        |
  | risk_level       |
  +------------------+
```

---

## Setup & Running the System

The entire system - API, worker, Redis, PostgreSQL - starts with a single docker command.

### Prerequisites
- Docker & Docker Compose installed.

### Step 1: Clone and Configure
In the root directory, configure your Gemini API Key in the `.env` file:
```env
GEMINI_API_KEY=your_gemini_api_key_here
```
*(If no API Key is provided, the pipeline automatically falls back to a simulated/mock classification and narrative summary model so that the pipeline still runs and completes successfully).*

### Step 2: Spin Up Containers
Run the following command to build and launch all services:
```bash
docker compose up --build
```

The services will start on:
- **FastAPI Web Server & Static Dashboard**: [http://localhost:8000](http://localhost:8000)
- **PostgreSQL Database**: `localhost:5432`
- **Redis Queue Broker**: `localhost:6379`

---

## API Endpoints & Example Curl Requests

### 1. Upload CSV File
Accepts a `.csv` file upload and enqueues the processing job.
```bash
curl -X POST -F "file=@transactions.csv" http://localhost:8000/jobs/upload
```
**Response:**
```json
{
  "job_id": "b3e34b12-9c1a-4d7a-8f25-8d5f39d1b0fc",
  "status": "pending",
  "message": "CSV uploaded successfully. Processing enqueued."
}
```

### 2. Get Job Status
Returns the status of the job. If completed, includes high-level summary statistics.
```bash
curl -X GET http://localhost:8000/jobs/b3e34b12-9c1a-4d7a-8f25-8d5f39d1b0fc/status
```
**Response:**
```json
{
  "job_id": "b3e34b12-9c1a-4d7a-8f25-8d5f39d1b0fc",
  "status": "completed",
  "filename": "transactions.csv",
  "row_count_raw": 97,
  "row_count_clean": 91,
  "created_at": "2026-07-01T03:00:00Z",
  "completed_at": "2026-07-01T03:00:12Z",
  "error_message": null,
  "summary": {
    "total_spend_inr": 498302.25,
    "total_spend_usd": 15420.50,
    "top_merchants": [
      {"merchant": "Flipkart", "spend": 146100.68, "count": 12},
      {"merchant": "Swiggy", "spend": 87342.10, "count": 15},
      {"merchant": "Ola", "spend": 62450.32, "count": 10}
    ],
    "anomaly_count": 4,
    "narrative": "Spending was heavily concentrated on domestic shopping and travel platforms Swiggy and Flipkart. There are 4 flagged anomalies related to statistical amount outliers and currency discrepancies.",
    "risk_level": "medium"
  }
}
```

### 3. Get Job Results
Returns the full transaction details and classifications.
```bash
curl -X GET http://localhost:8000/jobs/b3e34b12-9c1a-4d7a-8f25-8d5f39d1b0fc/results
```

### 4. List All Jobs
Lists all previous pipeline jobs.
```bash
curl -X GET http://localhost:8000/jobs
# Or filter by status
curl -X GET http://localhost:8000/jobs?status=completed
```

---

## Design Choices & Typography

The dashboard UI was designed around modern usability principles:
- **Togglable Themes**: Includes a theme toggler supporting both **Dark Mode** and **Light (White) Mode** to fit different user preferences.
- **Clean Typography**: Uses the classic **"Times New Roman"** font family as requested, styled with variable weights to maintain an authoritative, clean, and highly readable look.
- **Base Font Scaling**: Base body text is set to **11pt** (approx 14.6px), with headings and layout components scaling proportionally.
- **Visual Dashboards**: Integrates Chart.js to show dual-axis spending in INR/USD per category, and formats flagged anomalies with high-contrast red warnings to immediately highlight high-risk records.
