import os
import json
import time
import logging
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from app.config import settings

# Setup logging
logger = logging.getLogger("pipeline.llm")
logging.basicConfig(level=logging.INFO)

# Initialise Gemini
api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
is_mock = False

if not api_key:
    logger.warning("GEMINI_API_KEY not configured. Running in MOCK/SIMULATION mode.")
    is_mock = True
else:
    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        logger.error(f"Error configuring Gemini client: {e}. Falling back to mock mode.")
        is_mock = True

def retry_with_backoff(retries: int = 3, backoff_factor: float = 2.0, initial_delay: float = 1.0):
    """
    Decorator/Helper to retry functions with exponential backoff.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries:
                        logger.error(f"Failed after {retries} retries: {e}")
                        raise e
                    logger.warning(f"Attempt {attempt + 1} failed with error: {e}. Retrying in {delay:.2f}s...")
                    time.sleep(delay)
                    delay *= backoff_factor
        return wrapper
    return decorator

@retry_with_backoff(retries=3, backoff_factor=2.0, initial_delay=1.0)
def _call_gemini_api(prompt: str, json_mode: bool = True) -> str:
    """
    Call Gemini API with retry logic.
    """
    if is_mock:
        raise ValueError("Mock mode active")

    model = genai.GenerativeModel("gemini-1.5-flash")
    generation_config = {}
    if json_mode:
        generation_config = {"response_mime_type": "application/json"}
        
    response = model.generate_content(prompt, generation_config=generation_config)
    return response.text

def batch_classify_transactions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Batch classifies a list of uncategorised transactions.
    Each transaction dict must contain 'id', 'merchant', 'amount', 'currency', and 'notes'.
    Returns a list of dicts with 'id', 'category', and 'llm_raw_response'.
    If LLM calls fail after retries, returns results with llm_failed=True.
    """
    if not transactions:
        return []

    # If mock mode is active, simulate classification
    if is_mock:
        return _simulate_batch_classify(transactions)

    # Categories to select from:
    # Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, or Other
    prompt = f"""
    You are an expert financial transaction classification assistant.
    You will be given a list of transactions in JSON format.
    Your task is to classify each transaction into one of these categories:
    - Food
    - Shopping
    - Travel
    - Transport
    - Utilities
    - Cash Withdrawal
    - Entertainment
    - Other

    Rules:
    1. Swiggy, Zomato, Starbucks, McDonald's should be classified as "Food".
    2. Flipkart, Amazon, Zara, Nike should be "Shopping".
    3. MakeMyTrip, Bookings, AirIndia, IRCTC, Hotels should be "Travel".
    4. Ola, Uber, Lyft, Metro, Auto, Petrol, Gas, Fuel should be "Transport".
    5. Jio Recharge, Electricity, Gas Bill, Water, Internet, Phone, Airtel should be "Utilities".
    6. HDFC ATM, ATM, Cash, Withdraw, ATM Withdrawal should be "Cash Withdrawal".
    7. BookMyShow, Netflix, Spotify, Cinema, Theatre, Disneyland, Disney+ should be "Entertainment".
    8. Any transaction that does not fit these categories or is unclear should be "Other".

    Input JSON transactions:
    {json.dumps(transactions)}

    Format your output strictly as a JSON list of objects. Each object must contain exactly:
    - "id": the integer transaction ID from the input.
    - "category": the classified category string (must be exactly one of the eight listed above).
    
    Example Output:
    [
      {{"id": 100, "category": "Food"}},
      {{"id": 101, "category": "Shopping"}}
    ]
    """
    
    try:
        raw_response = _call_gemini_api(prompt, json_mode=True)
        logger.info(f"Gemini raw response for classification: {raw_response}")
        classifications = json.loads(raw_response)
        
        # Build mapping for lookup
        results = []
        for c in classifications:
            results.append({
                "id": c.get("id"),
                "category": c.get("category", "Other"),
                "llm_raw_response": raw_response,
                "llm_failed": False
            })
        return results
    except Exception as e:
        logger.error(f"Failed to batch classify transactions using Gemini: {e}")
        # Return fallback results marked as failed
        return [{
            "id": t.get("id"),
            "category": "Uncategorised",
            "llm_raw_response": str(e),
            "llm_failed": True
        } for t in transactions]

def generate_spending_summary(
    total_spend_inr: float,
    total_spend_usd: float,
    top_merchants: List[Dict[str, Any]],
    anomaly_count: int,
    transactions_summary: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Calls Gemini API to generate a narrative spending summary and a risk assessment.
    Returns a dict with total_spend_inr, total_spend_usd, top_merchants, anomaly_count, narrative, risk_level.
    """
    # Pre-calculated stats to pass to the LLM
    data_context = {
        "total_spend_inr": total_spend_inr,
        "total_spend_usd": total_spend_usd,
        "top_merchants": top_merchants,
        "anomaly_count": anomaly_count,
        "sample_transactions": transactions_summary[:15] # Send a sample of transactions for context
    }

    if is_mock:
        return _simulate_spending_summary(data_context)

    prompt = f"""
    You are a senior financial analyst and fraud detection expert.
    Analyze the following financial transaction summary data and write a professional narrative summary and risk assessment.

    Context Data:
    {json.dumps(data_context, indent=2)}

    Your tasks:
    1. Write a 2-3 sentence narrative summarizing the spending patterns, highlighting the top categories/merchants, and noting if there are any suspicious trends or anomalies.
    2. Assess the risk level. Choose exactly one of: "low", "medium", "high".
       - If there are zero anomalies and standard spending, risk is "low".
       - If there are 1-2 anomalies or minor currency irregularities, risk is "medium".
       - If there are multiple anomalies, extremely large transaction outliers, or flagrant irregularities, risk is "high".

    Format your output strictly as a JSON object with the following fields:
    - "total_spend_inr": {total_spend_inr} (keep this exact value)
    - "total_spend_usd": {total_spend_usd} (keep this exact value)
    - "top_merchants": the JSON list of top merchants provided in the context
    - "anomaly_count": {anomaly_count} (keep this exact value)
    - "narrative": your 2-3 sentence spending narrative text.
    - "risk_level": "low", "medium", or "high" based on your assessment.

    Example Output:
    {{
      "total_spend_inr": 45000.5,
      "total_spend_usd": 120.0,
      "top_merchants": [{{"merchant": "Amazon", "spend": 25000.0, "count": 3}}],
      "anomaly_count": 1,
      "narrative": "The spending is heavily dominated by online shopping on Amazon. There is one anomaly detected due to a high amount transaction. Overall risk remains medium due to the isolated outlier.",
      "risk_level": "medium"
    }}
    """
    
    try:
        raw_response = _call_gemini_api(prompt, json_mode=True)
        logger.info(f"Gemini raw response for summary: {raw_response}")
        summary_data = json.loads(raw_response)
        
        # Ensure required keys exist and match pre-calculated stats
        return {
            "total_spend_inr": total_spend_inr,
            "total_spend_usd": total_spend_usd,
            "top_merchants": top_merchants,
            "anomaly_count": anomaly_count,
            "narrative": summary_data.get("narrative", "Unable to generate narrative."),
            "risk_level": summary_data.get("risk_level", "medium").lower()
        }
    except Exception as e:
        logger.error(f"Failed to generate narrative summary using Gemini: {e}")
        # Fallback to programmatic default
        risk = "low"
        if anomaly_count > 2:
            risk = "high"
        elif anomaly_count > 0:
            risk = "medium"
            
        return {
            "total_spend_inr": total_spend_inr,
            "total_spend_usd": total_spend_usd,
            "top_merchants": top_merchants,
            "anomaly_count": anomaly_count,
            "narrative": f"Spending totals are INR {total_spend_inr:,.2f} and USD {total_spend_usd:,.2f}. Top merchants are {', '.join([m['merchant'] for m in top_merchants[:3]])}. There are {anomaly_count} flagged anomalies.",
            "risk_level": risk
        }

# --- Mock Fallbacks (for testing or when API key is missing) ---

def _simulate_batch_classify(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Locally classifies transactions using simple keyword matching if API key is not present.
    """
    logger.info("Using simulated batch classification.")
    results = []
    
    food_keywords = {"swiggy", "zomato", "starbucks", "mcdonald", "food", "restaurant", "pizza"}
    shopping_keywords = {"flipkart", "amazon", "zara", "nike", "shopping", "walmart", "mall"}
    travel_keywords = {"makemytrip", "booking", "airindia", "irctc", "hotel", "travel", "flight"}
    transport_keywords = {"ola", "uber", "lyft", "metro", "auto", "petrol", "gas", "fuel", "cab"}
    utilities_keywords = {"jio", "recharge", "electricity", "bill", "water", "internet", "phone", "airtel"}
    cash_keywords = {"hdfc atm", "atm", "cash", "withdraw"}
    ent_keywords = {"bookmyshow", "netflix", "spotify", "cinema", "theatre", "disney"}

    for t in transactions:
        merchant = str(t.get("merchant", "")).lower()
        notes = str(t.get("notes", "")).lower()
        text = f"{merchant} {notes}"
        
        category = "Other"
        if any(kw in text for kw in food_keywords):
            category = "Food"
        elif any(kw in text for kw in shopping_keywords):
            category = "Shopping"
        elif any(kw in text for kw in travel_keywords):
            category = "Travel"
        elif any(kw in text for kw in transport_keywords):
            category = "Transport"
        elif any(kw in text for kw in utilities_keywords):
            category = "Utilities"
        elif any(kw in text for kw in cash_keywords):
            category = "Cash Withdrawal"
        elif any(kw in text for kw in ent_keywords):
            category = "Entertainment"
            
        results.append({
            "id": t.get("id"),
            "category": category,
            "llm_raw_response": "Simulated local LLM response",
            "llm_failed": False
        })
        
    return results

def _simulate_spending_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Locally generates a narrative summary and risk assessment if API key is not present.
    """
    logger.info("Using simulated spending summary.")
    anomaly_count = context.get("anomaly_count", 0)
    total_spend_inr = context.get("total_spend_inr", 0.0)
    total_spend_usd = context.get("total_spend_usd", 0.0)
    top_merchants = context.get("top_merchants", [])
    
    merchant_names = [m.get("merchant", "") for m in top_merchants[:3]]
    merchants_str = ", ".join(merchant_names) if merchant_names else "none"
    
    risk_level = "low"
    if anomaly_count > 2:
        risk_level = "high"
    elif anomaly_count > 0:
        risk_level = "medium"
        
    narrative = (
        f"Aggregated spending shows total expenditures of INR {total_spend_inr:,.2f} and USD {total_spend_usd:,.2f}. "
        f"The top merchants contributing to this activity are {merchants_str}. "
        f"A total of {anomaly_count} transactions were flagged as anomalies, resulting in a {risk_level} risk level assessment."
    )
    
    return {
        "total_spend_inr": total_spend_inr,
        "total_spend_usd": total_spend_usd,
        "top_merchants": top_merchants,
        "anomaly_count": anomaly_count,
        "narrative": narrative,
        "risk_level": risk_level
    }
