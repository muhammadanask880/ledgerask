"""Password hashing and JWT-based session cookies."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_session_token(user_id: int, *, remember_me: bool = False) -> str:
    days = settings.remember_me_max_age_days if remember_me else settings.session_max_age_days
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    payload = {"sub": str(user_id), "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except jwt.PyJWTError:
        return None


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Returns the logged-in user, or None - never raises. Use this in page
    routes so you can decide yourself whether to redirect to /login."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    user_id = decode_session_token(token)
    if user_id is None:
        return None
    return db.get(User, user_id)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Use this in JSON API routes - raises 401 if not logged in."""
    user = get_optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
