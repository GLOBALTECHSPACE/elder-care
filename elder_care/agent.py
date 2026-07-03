import re
import os
import json
import logging
from datetime import datetime
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import Workflow, START, node
from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters
from google.genai import types

from elder_care.config import config

# Setup paths
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(APP_DIR)
AUDIT_LOG_PATH = os.path.join(PROJECT_DIR, "security_audit.json")

# Initialize models
model_instance = Gemini(
    model=config.model,
    retry_options=types.HttpRetryOptions(attempts=3),
)

# ---------------------------------------------------------------------------
# MCP Toolset Setup
# ---------------------------------------------------------------------------
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", "elder_care/mcp_server.py"],
        )
    )
)

# ---------------------------------------------------------------------------
# Specialized Sub-Agents
# ---------------------------------------------------------------------------
medication_agent = Agent(
    name="medication_agent",
    model=model_instance,
    rerun_on_resume=True,
    instruction=(
        "You are the Medication Manager Agent. Your role is to help log medication times, "
        "check medication schedules, and verify dosage. Always query the medication schedule "
        "or log intake using your tools when asked. If recording an intake, provide a clear success message."
    ),
    tools=[mcp_toolset],
)

health_metrics_agent = Agent(
    name="health_metrics_agent",
    model=model_instance,
    rerun_on_resume=True,
    instruction=(
        "You are the Health Metrics Agent. Your role is to record and track basic health metrics "
        "like blood pressure, heart rate, blood sugar, and water intake. When a user asks to record "
        "or view metrics, use your tools. If blood pressure or heart rate is abnormal, warn the "
        "caregiver gently but clearly."
    ),
    tools=[mcp_toolset],
)

cognitive_games_agent = Agent(
    name="cognitive_games_agent",
    model=model_instance,
    rerun_on_resume=True,
    instruction=(
        "You are the Cognitive Games Agent. Your role is to suggest and guide the user through "
        "interactive cognitive games. Use your tools to retrieve the list of available games. "
        "Suggest a specific game and explain how to play it. Keep your tone cheerful and encouraging."
    ),
    tools=[mcp_toolset],
)

# ---------------------------------------------------------------------------
# Orchestrator Agent & Dynamic State Request Tool
# ---------------------------------------------------------------------------
def request_log_confirmation(ctx: Context, details: str) -> str:
    """Request caregiver confirmation before recording a medication intake or health metric log.
    
    Args:
        details: The details of what is being recorded (e.g. 'Blood Pressure 140/90 mmHg' or 'Donepezil taken').
    """
    ctx.state["needs_confirmation"] = True
    ctx.state["temp_log_item"] = details
    return f"Prepared log request for: '{details}'. Awaiting human confirmation."

orchestrator_agent = Agent(
    name="orchestrator_agent",
    model=model_instance,
    rerun_on_resume=True,
    instruction=(
        "You are the ElderCare Orchestrator Agent. Your role is to coordinate care for seniors. "
        "You have access to specialized sub-agents: medication_agent, health_metrics_agent, and cognitive_games_agent. "
        "Delegate any specific queries to the correct sub-agent.\n\n"
        "IMPORTANT SECURITY / PROTOCOL RULE:\n"
        "If the user is requesting to log, record, or add a medication intake or health metric, you MUST "
        "call the `request_log_confirmation` tool with the details of the log entry BEFORE delegating to any agent "
        "or calling any direct record tool. Do NOT bypass this confirmation request."
    ),
    tools=[
        AgentTool(medication_agent),
        AgentTool(health_metrics_agent),
        AgentTool(cognitive_games_agent),
        request_log_confirmation,
    ],
)

# ---------------------------------------------------------------------------
# Security Node Functions & Helpers
# ---------------------------------------------------------------------------
def write_audit_log(severity: str, message: str, event_type: str = "security_check"):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "severity": severity,
        "message": message
    }
    print(f"AUDIT_LOG: {json.dumps(log_entry)}")
    try:
        logs = []
        if os.path.exists(AUDIT_LOG_PATH):
            with open(AUDIT_LOG_PATH, "r") as f:
                logs = json.load(f)
        logs.append(log_entry)
        with open(AUDIT_LOG_PATH, "w") as f:
            json.dump(logs, f, indent=4)
    except Exception:
        pass

@node
async def security_checkpoint(ctx: Context, node_input: Any):
    query = str(node_input)
    
    # 1. PII Scrubbing
    pii_patterns = {
        "SSN": r'\b\d{3}-\d{2}-\d{4}\b',
        "Phone": r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
        "Medicare ID": r'\b[1-9][A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{4}\b'
    }
    scrubbed_query = query
    scrubbed_any = False
    for name, pattern in pii_patterns.items():
        if re.search(pattern, scrubbed_query):
            scrubbed_query = re.sub(pattern, "[REDACTED]", scrubbed_query)
            scrubbed_any = True
            
    if scrubbed_any:
        write_audit_log("WARNING", f"PII detected and redacted: {scrubbed_query}", "pii_redaction")
    
    # 2. Prompt Injection Detection
    injection_keywords = ["ignore previous instructions", "system prompt", "override security", "act as a dev", "ignore security"]
    for keyword in injection_keywords:
        if keyword in query.lower():
            write_audit_log("CRITICAL", f"Prompt injection attempt detected: '{keyword}'", "prompt_injection")
            ctx.route = "SECURITY_EVENT"
            return "Security violation: Prompt injection attempt detected. Access denied."
            
    # 3. Domain-Specific Security Rule: Financial Transactions Blocked
    financial_keywords = ["wire money", "bank transfer", "credit card pin", "password reset", "send funds", "buy stock"]
    for keyword in financial_keywords:
        if keyword in query.lower():
            write_audit_log("CRITICAL", f"Financial transaction request blocked: '{keyword}'", "domain_violation")
            ctx.route = "SECURITY_EVENT"
            return "Security violation: Financial transactions or credential requests are not permitted by this agent."
            
    write_audit_log("INFO", "Input verification passed. Input is clean.", "clean_input")
    ctx.route = "CLEAN"
    ctx.state["user_query"] = scrubbed_query
    return scrubbed_query

@node
async def security_violation_handler(ctx: Context, node_input: Any):
    return node_input

# ---------------------------------------------------------------------------
# Orchestrator Node with Human-In-The-Loop Confirmation
# ---------------------------------------------------------------------------
def _direct_log_item(item: str):
    """Directly records confirmed logs to the caregiver records json database."""
    records_path = os.path.join(PROJECT_DIR, "caregiver_records.json")
    data = {}
    if os.path.exists(records_path):
        try:
            with open(records_path, "r") as f:
                data = json.load(f)
        except Exception:
            pass
            
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Simple heuristics to route the log into correct JSON section
    item_lower = item.lower()
    if any(m in item_lower for m in ["pressure", "heart", "pulse", "sugar", "glucose", "temp", "fever", "water"]):
        # Parse value and unit if possible, or save details
        log_entry = {
            "timestamp": timestamp_str,
            "metric_type": "Health Metric Log",
            "value": item,
            "unit": "",
            "status": "Recorded"
        }
        data.setdefault("health_metrics", []).append(log_entry)
    else:
        log_entry = {
            "timestamp": timestamp_str,
            "medication": item,
            "status": "taken"
        }
        data.setdefault("medication_logs", []).append(log_entry)
        
    with open(records_path, "w") as f:
        json.dump(data, f, indent=4)

@node(rerun_on_resume=True)
async def run_orchestrator(ctx: Context, node_input: Any):
    # 1. Check if we are resuming from a pending caregiver confirmation
    if ctx.state.get("awaiting_confirmation"):
        ctx.state["awaiting_confirmation"] = False
        user_response = str(node_input).strip().lower()
        pending_item = ctx.state.get("temp_log_item")
        
        if user_response in ["yes", "y", "confirm"]:
            # Commit the logged item
            if pending_item:
                _direct_log_item(pending_item)
                ctx.state["temp_log_item"] = None
                return f"✅ Confirmation received. Recorded: '{pending_item}' successfully in logs."
            return "No pending item found to log."
        else:
            ctx.state["temp_log_item"] = None
            return f"❌ Log request cancelled. Did not record: '{pending_item}'."

    # 2. Regular user query processing
    user_query = ctx.state.get("user_query", node_input)
    
    # Run the orchestrator agent
    response = await ctx.run_node(orchestrator_agent, node_input=user_query)
    
    # 3. Check if security or confirmation flag was raised
    if ctx.state.get("needs_confirmation"):
        ctx.state["needs_confirmation"] = False
        ctx.state["awaiting_confirmation"] = True
        # Suspend workflow and return RequestInput event
        return RequestInput(
            message=f"✋ Caregiver confirmation required. Please confirm: do you want to record '{ctx.state.get('temp_log_item')}'? (yes/no)",
            response_schema=str
        )
        
    return response

@node
async def final_output(ctx: Context, node_input: Any):
    return node_input

# ---------------------------------------------------------------------------
# Workflow Compiler & App definition
# ---------------------------------------------------------------------------
root_agent = Workflow(
    name="elder_care_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {
            "CLEAN": run_orchestrator,
            "SECURITY_EVENT": security_violation_handler
        }),
        (run_orchestrator, final_output),
        (security_violation_handler, final_output)
    ]
)

app = App(
    root_agent=root_agent,
    name="elder_care",
)
