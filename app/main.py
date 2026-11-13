"""FastAPI application: auth pages, static marketing pages, and the chat API."""
import json

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent
from app.auth import (
    create_session_token,
    get_current_user,
    get_optional_user,
    hash_password,
    verify_password,
)
from app.config import settings, BASE_DIR
from app.database import SessionLocal, get_db, init_db
from app.models import ChatMessage, Conversation, User
from app.schemas import ChatRequest

MAX_MESSAGE_LENGTH = 2000
TITLE_MAX_LENGTH = 48


def _derive_title(message: str) -> str:
    """Auto-names a new conversation from its first message, the same way
    ChatGPT/Claude do - truncate to a readable length, no trailing partial word."""
    cleaned = " ".join(message.split())
    if len(cleaned) <= TITLE_MAX_LENGTH:
        return cleaned
    return cleaned[:TITLE_MAX_LENGTH].rsplit(" ", 1)[0] + "…"

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LedgerAsk", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

MAX_HISTORY_MESSAGES = 20


def _set_session_cookie(response: RedirectResponse, user_id: int, remember_me: bool) -> None:
    token = create_session_token(user_id, remember_me=remember_me)
    max_age_days = settings.remember_me_max_age_days if remember_me else settings.session_max_age_days
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max_age_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        # secure=True should be turned on once this is served over HTTPS.
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: User | None = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "index.html", {"user": user})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request, user: User | None = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "about.html", {"user": user})


@app.get("/contact", response_class=HTMLResponse)
def contact(request: Request, user: User | None = Depends(get_optional_user)):
    return templates.TemplateResponse(request, "contact.html", {"user": user})


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, user: User | None = Depends(get_optional_user)):
    if user:
        return RedirectResponse("/chat", status_code=303)
    return templates.TemplateResponse(request, "signup.html", {"error": None, "user": None})


@app.post("/signup", response_class=HTMLResponse)
def signup_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()

    def error(message: str):
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": message, "user": None, "full_name": full_name, "email": email},
            status_code=400,
        )

    if len(password) < 8:
        return error("Password must be at least 8 characters.")
    if password != confirm_password:
        return error("Passwords do not match.")

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return error("An account with that email already exists.")

    user = User(email=email, full_name=full_name.strip(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/chat", status_code=303)
    _set_session_cookie(response, user.id, remember_me=False)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(get_optional_user)):
    if user:
        return RedirectResponse("/chat", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None, "user": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(False),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Incorrect email or password.", "user": None, "email": email},
            status_code=400,
        )

    response = RedirectResponse("/chat", status_code=303)
    _set_session_cookie(response, user.id, remember_me=remember_me)
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response


def _user_conversations(db: Session, user_id: int) -> list[Conversation]:
    return (
        db.execute(select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc()))
        .scalars()
        .all()
    )


@app.get("/chat")
def chat_root(user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    convo = db.execute(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc())
    ).scalars().first()
    if convo is None:
        convo = Conversation(user_id=user.id)
        db.add(convo)
        db.commit()
        db.refresh(convo)
    return RedirectResponse(f"/chat/{convo.id}", status_code=303)


@app.post("/chat/new")
def chat_new(user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    convo = Conversation(user_id=user.id)
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return RedirectResponse(f"/chat/{convo.id}", status_code=303)


@app.post("/chat/{conversation_id}/delete")
def chat_delete(conversation_id: int, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    convo = db.get(Conversation, conversation_id)
    if convo is not None and convo.user_id == user.id:
        db.delete(convo)
        db.commit()
    return RedirectResponse("/chat", status_code=303)


@app.get("/chat/{conversation_id}", response_class=HTMLResponse)
def chat_page(
    conversation_id: int,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)

    convo = db.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    history_view = [
        {
            "role": m.role,
            "content": m.content,
            "trace": json.loads(m.trace_json) if m.trace_json else [],
        }
        for m in convo.messages[-MAX_HISTORY_MESSAGES:]
    ]
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "user": user,
            "history": history_view,
            "conversation": convo,
            "conversations": _user_conversations(db, user.id),
        },
    )


@app.post("/api/chat")
def chat_api(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Streams newline-delimited JSON events as the agent works: a tool being
    called, that tool's result (the proof-panel data), and the final answer
    in small chunks so the UI can type it out. One line in, one line out -
    no buffering the whole answer before the user sees anything."""
    question = payload.message.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(question) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=400, detail=f"Message is too long (max {MAX_MESSAGE_LENGTH} characters).")

    convo = db.get(Conversation, payload.conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    prior = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == convo.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(MAX_HISTORY_MESSAGES)
        )
        .scalars()
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(prior)]

    # Persist the user's turn up front, before streaming starts, so it's
    # never lost even if the connection drops mid-answer. First message in a
    # conversation also auto-names it, same as ChatGPT/Claude.
    is_first_message = len(prior) == 0
    if is_first_message:
        convo.title = _derive_title(question)
    convo.updated_at = datetime.utcnow()
    db.add(ChatMessage(conversation_id=convo.id, role="user", content=question))
    db.commit()

    conversation_id = convo.id
    user_email = user.email
    auto_title = convo.title if is_first_message else None

    def event_stream():
        if auto_title:
            yield json.dumps({"type": "title", "title": auto_title}) + "\n"

        answer_parts: list[str] = []
        trace: list[dict] = []
        try:
            for event in agent.ask_stream(question, user_email=user_email, history=history):
                if event["type"] == "answer_chunk":
                    answer_parts.append(event["text"])
                elif event["type"] == "tool_result":
                    trace.append(event["step"])
                yield json.dumps(event, default=str) + "\n"
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as an error event
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
            return

        final_answer = "".join(answer_parts)
        # A fresh session, independent of the request-scoped one above, since
        # that one may already be torn down by the time this generator
        # finishes draining after the response has started streaming.
        with SessionLocal() as write_db:
            write_db.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=final_answer,
                    trace_json=json.dumps(trace, default=str),
                )
            )
            write_db.commit()

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
