"""
Census Chat — FastAPI server (Railway deployment).

Replaces AgentCore Runtime with a standard FastAPI app that has
equivalent logic: guardrails, ChromaDB RAG, Bedrock LLM, in-memory memory.

Endpoints:
  POST /chat           — main chat endpoint
  GET  /health         — health check
"""
import os, json, logging, uuid, time
from typing import Optional
from pathlib import Path
from collections import defaultdict

import boto3
import chromadb
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
REGION          = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID        = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")  # swap to us.anthropic.claude-opus-5-5 once Anthropic verification clears
GUARDRAIL_ID    = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VER   = os.environ.get("GUARDRAIL_VERSION", "1")
CHROMA_PATH     = os.environ.get("CHROMA_PATH", str(Path(__file__).parent / "chroma_db"))
API_TOKEN       = os.environ.get("API_TOKEN", "census-chat-demo-token")  # simple auth

SYSTEM_PROMPT = """You are an expert on US Census demographics data.
You help users explore and understand US population statistics from the \
American Community Survey (ACS) 2020 5-year estimates, covering all US counties.

You have access to county and state-level data including:
- Population size, age distribution, and gender breakdown
- Race and ethnicity demographics
- Median household income and poverty rates
- Housing units, homeownership, home values, and rent
- Employment and unemployment rates
- Educational attainment

STRICT RULES:
1. Answer ONLY from the census data provided in the <context> tags below.
2. If data is not in the context, say "I don't have that specific data" — do NOT invent numbers.
3. Always cite the geographic area and note this is ACS 2020 data.
4. Format dollar amounts with $ and commas. Round percentages to 1 decimal place.
5. If the question is ambiguous (e.g. "What about income?"), ask which geography.
6. If the question is unrelated to US Census data, politely decline."""

# ── In-memory session store (replaces AgentCore Memory short-term) ────────────
_sessions: dict[str, list[dict]] = defaultdict(list)
_MAX_TURNS = 10

def get_history(session_id: str) -> list[dict]:
    return _sessions[session_id][-_MAX_TURNS*2:]

def add_to_history(session_id: str, role: str, content: str):
    _sessions[session_id].append({"role": role, "content": content})

# ── ChromaDB ──────────────────────────────────────────────────────────────────
_chroma_collection = None

def get_collection():
    global _chroma_collection
    if _chroma_collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        _chroma_collection = client.get_collection("census-counties")
        logger.info(f"ChromaDB loaded: {_chroma_collection.count()} docs")
    return _chroma_collection

def retrieve_census_context(query: str, top_k: int = 5) -> str:
    try:
        col = get_collection()
        results = col.query(
            query_texts=[query],
            n_results=top_k,
        )
        docs = results.get("documents", [[]])[0]
        if not docs:
            return "No census data found for that query. Try asking about a specific state or county."
        return "\n\n---\n\n".join(docs)
    except Exception as e:
        logger.error(f"ChromaDB error: {e}")
        return "Census data temporarily unavailable. Please try again."

# ── Bedrock ───────────────────────────────────────────────────────────────────
_bedrock_client = None

def get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock_client

def apply_guardrail(text: str, source: str = "INPUT") -> tuple[bool, str]:
    """Returns (blocked, message). blocked=True means content was filtered."""
    if not GUARDRAIL_ID:
        return False, text
    try:
        br = boto3.client("bedrock-runtime", region_name=REGION)
        resp = br.apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VER,
            source=source,
            content=[{"text": {"text": text}}],
        )
        if resp.get("action") == "GUARDRAIL_INTERVENED":
            outputs = resp.get("outputs", [])
            msg = outputs[0]["text"] if outputs else (
                "I can only answer questions about US Census data. "
                "Please ask about population, demographics, income, housing, or employment."
            )
            return True, msg
        return False, text
    except Exception as e:
        logger.warning(f"Guardrail error (fail-open): {e}")
        return False, text

def call_bedrock(messages: list[dict], system: str) -> str:
    br = get_bedrock()
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "system": system,
        "messages": messages,
    })
    try:
        resp = br.invoke_model(
            modelId=MODEL_ID,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(resp["body"].read())
        return result["content"][0]["text"]
    except br.exceptions.ValidationException as e:
        err = str(e)
        if "Operation not allowed" in err or "not authorized" in err.lower():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Bedrock model access not yet enabled for this account. "
                    "Please visit https://us-east-1.console.aws.amazon.com/bedrock/home"
                    "?region=us-east-1#/modelaccess to enable Claude Opus 5.5."
                )
            )
        raise HTTPException(status_code=503, detail=f"LLM error: {err[:200]}")
    except Exception as e:
        logger.error(f"Bedrock error: {e}")
        raise HTTPException(status_code=503, detail=f"LLM unavailable: {str(e)[:200]}")

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Census Chat API",
    description="US Census data chat agent",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── Request/Response models ───────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    actor_id: Optional[str] = "anonymous"

class ChatResponse(BaseModel):
    response: str
    session_id: str
    blocked: bool = False

# ── Auth ──────────────────────────────────────────────────────────────────────
def verify_token(authorization: str = Header(default="")):
    """Simple token auth. Accepts Bearer <token> or skips if no API_TOKEN set."""
    if not API_TOKEN or API_TOKEN == "open":
        return True
    token = authorization.replace("Bearer ", "").strip()
    if token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return True

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        col = get_collection()
        return {"status": "ok", "docs": col.count(), "model": MODEL_ID}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, _: bool = Depends(verify_token)):
    t0 = time.time()
    session_id = req.session_id or str(uuid.uuid4())
    user_msg = req.message.strip()

    if not user_msg:
        raise HTTPException(status_code=400, detail="message is required")

    logger.info(f"[{session_id[:8]}] {user_msg[:80]}")

    # 1. Guardrail on input
    blocked, msg = apply_guardrail(user_msg, "INPUT")
    if blocked:
        return ChatResponse(response=msg, session_id=session_id, blocked=True)

    # 2. Retrieve census context
    context = retrieve_census_context(user_msg)

    # 3. Build messages with history
    history = get_history(session_id)
    system = f"{SYSTEM_PROMPT}\n\n<context>\n{context}\n</context>"
    messages = history + [{"role": "user", "content": user_msg}]

    # 4. Call LLM
    response_text = call_bedrock(messages, system)

    # 5. Guardrail on output
    blocked_out, response_text = apply_guardrail(response_text, "OUTPUT")
    if blocked_out:
        response_text = (
            "I found related census data but couldn't form a reliable answer. "
            "Please try rephrasing your question."
        )

    # 6. Store to session memory
    add_to_history(session_id, "user", user_msg)
    add_to_history(session_id, "assistant", response_text)

    logger.info(f"[{session_id[:8]}] {time.time()-t0:.1f}s")
    return ChatResponse(response=response_text, session_id=session_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
