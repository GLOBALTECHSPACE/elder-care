import os
import json
from datetime import datetime
from mcp.server.fastmcp import FastMCP

# Define the FastMCP server
mcp = FastMCP("elder-care-mcp-server")

# Resolve database file path relative to this script
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(APP_DIR)
RECORDS_PATH = os.path.join(PROJECT_DIR, "caregiver_records.json")

def _load_records():
    if not os.path.exists(RECORDS_PATH):
        # Initialize default records
        default_data = {
            "medication_schedule": [
                {"medication": "Donepezil", "dosage": "5mg", "time": "8:00 AM", "purpose": "Cognitive enhancement"},
                {"medication": "Memantine", "dosage": "10mg", "time": "8:00 PM", "purpose": "Reduce confusion"},
                {"medication": "Vitamin D3", "dosage": "1000 IU", "time": "12:00 PM", "purpose": "Bone health"}
            ],
            "medication_logs": [],
            "health_metrics": [
                {"timestamp": "2026-07-03 08:30:00", "metric_type": "Blood Pressure", "value": "120/80", "unit": "mmHg", "status": "Normal"},
                {"timestamp": "2026-07-03 08:32:00", "metric_type": "Heart Rate", "value": "72", "unit": "bpm", "status": "Normal"}
            ],
            "cognitive_games": [
                {"name": "Trivia Quiz", "description": "General knowledge questions to stimulate memory retrieval."},
                {"name": "Word Association", "description": "Forming connections between word prompts to exercise verbal fluency."},
                {"name": "Memory Challenge", "description": "Recalling sequences of objects or numbers."},
                {"name": "Mental Math", "description": "Simple arithmetic equations to test focus and processing speed."}
            ]
        }
        with open(RECORDS_PATH, "w") as f:
            json.dump(default_data, f, indent=4)
        return default_data

    try:
        with open(RECORDS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_records(data):
    with open(RECORDS_PATH, "w") as f:
        json.dump(data, f, indent=4)

@mcp.tool()
def get_medication_schedule() -> str:
    """Retrieve the patient's daily medication schedule including dosages and timings."""
    data = _load_records()
    schedule = data.get("medication_schedule", [])
    return json.dumps(schedule, indent=2)

@mcp.tool()
def log_medication_intake(medication_name: str, status: str = "taken") -> str:
    """Log when a patient takes or misses their scheduled medication.
    
    Args:
        medication_name: The name of the medication taken/missed.
        status: The intake status, e.g., 'taken' or 'missed'.
    """
    data = _load_records()
    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "medication": medication_name,
        "status": status
    }
    data.setdefault("medication_logs", []).append(log_entry)
    _save_records(data)
    return f"Successfully logged medication: {medication_name} as {status} at {log_entry['timestamp']}."

@mcp.tool()
def log_health_metric(metric_type: str, value: str, unit: str) -> str:
    """Record a patient health metric (e.g., blood pressure, heart rate, blood sugar, water intake).
    
    Args:
        metric_type: The type of health metric (e.g. 'Blood Pressure', 'Heart Rate', 'Blood Sugar', 'Water Intake').
        value: The value of the metric (e.g. '120/80', '72', '95', '250').
        unit: The measurement unit (e.g. 'mmHg', 'bpm', 'mg/dL', 'ml').
    """
    data = _load_records()
    
    # Assess status based on common thresholds
    status = "Normal"
    if metric_type.lower() == "blood pressure":
        try:
            systolic, diastolic = map(int, value.split("/"))
            if systolic >= 140 or diastolic >= 90:
                status = "High (Requires Attention)"
            elif systolic < 90 or diastolic < 60:
                status = "Low (Requires Attention)"
        except Exception:
            pass
    elif metric_type.lower() == "heart rate":
        try:
            hr = int(value)
            if hr > 100 or hr < 60:
                status = "Irregular (Requires Attention)"
        except Exception:
            pass
            
    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "metric_type": metric_type,
        "value": value,
        "unit": unit,
        "status": status
    }
    data.setdefault("health_metrics", []).append(log_entry)
    _save_records(data)
    return json.dumps({"message": f"Successfully logged {metric_type}.", "entry": log_entry}, indent=2)

@mcp.tool()
def get_health_metrics_history() -> str:
    """Retrieve the historical logs of recorded health metrics (blood pressure, heart rate, etc.)."""
    data = _load_records()
    metrics = data.get("health_metrics", [])
    return json.dumps(metrics, indent=2)

@mcp.tool()
def get_cognitive_games() -> str:
    """Retrieve the catalog of recommended cognitive games and mental exercise activities."""
    data = _load_records()
    games = data.get("cognitive_games", [])
    return json.dumps(games, indent=2)

if __name__ == "__main__":
    mcp.run("stdio")
