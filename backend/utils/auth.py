import asyncio
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator
import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from models.user import UserModel
from utils.database import get_db


auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)
ACCESS_COOKIE_NAME = "codegraph_access"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TOKEN_ISSUER = "codegraph-ai"
TOKEN_AUDIENCE = "codegraph-ai-web"

# OWASP's minimum Argon2id profile: 19 MiB memory, 2 iterations, 1 lane.
password_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)
DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-password")


def validate_password_strength(value: str) -> str:
    if not any(char.islower() for char in value) or not any(char.isupper() for char in value) or not any(char.isdigit() for char in value):
        raise ValueError("Password must include uppercase, lowercase, and numeric characters.")
    return value


def normalize_email(value: str) -> str:
    return value.strip().lower()


def hash_password(value: str) -> str:
    return password_hasher.hash(value)


def verify_password(value: str, encoded_hash: str) -> bool:
    try:
        return password_hasher.verify(encoded_hash, value)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user: UserModel) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user.id),
            "email": user.email,
            "ver": user.token_version,
            "type": "access",
            "jti": uuid.uuid4().hex,
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
        path="/",
    )


def clear_access_cookie(response: Response) -> None:
    response.delete_cookie(
        key=ACCESS_COOKIE_NAME,
        httponly=True,
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
        path="/",
    )


def require_allowed_cookie_origin(request: Request) -> None:
    if request.method in SAFE_METHODS or request.headers.get("authorization"):
        return
    origin = request.headers.get("origin")
    if not origin or not any(hmac.compare_digest(origin, allowed) for allowed in settings.cors_origins):
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def reject_cross_site_browser_request(request: Request) -> None:
    """Reject foreign browser form posts while preserving non-browser API clients."""
    origin = request.headers.get("origin")
    if origin:
        if not any(hmac.compare_digest(origin, allowed) for allowed in settings.cors_origins):
            raise HTTPException(status_code=403, detail="Request origin is not allowed.")
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site request is not allowed.")


class UserSignUp(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        return validate_password_strength(value)


class AuthStatus(BaseModel):
    message: str


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def strong_new_password(cls, value: str) -> str:
        return validate_password_strength(value)


def get_current_user(
    request: Request,
    bearer_token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = bearer_token or request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        raise credentials_exception
    if not bearer_token:
        require_allowed_cookie_origin(request)

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
        )
        user_id = int(payload.get("sub", ""))
        token_version = payload.get("ver")
        if payload.get("type") != "access":
            raise credentials_exception
    except (jwt.PyJWTError, TypeError, ValueError):
        raise credentials_exception

    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if user is None or token_version != user.token_version:
        raise credentials_exception
    return user


@auth_router.post("/signup", status_code=status.HTTP_201_CREATED, response_model=AuthStatus)
async def signup(user_data: UserSignUp, db: Session = Depends(get_db)):
    email = normalize_email(str(user_data.email))
    hashed_password = await asyncio.to_thread(hash_password, user_data.password)
    if db.query(UserModel).filter(UserModel.email == email).first():
        raise HTTPException(status_code=400, detail="Unable to create account with these credentials.")

    db.add(UserModel(email=email, hashed_password=hashed_password))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Unable to create account with these credentials.")
    return {"message": "User registered successfully"}


@auth_router.post("/login", response_model=AuthStatus)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    reject_cross_site_browser_request(request)
    email = normalize_email(form_data.username)
    user = db.query(UserModel).filter(UserModel.email == email).first()
    password_hash = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_valid = await asyncio.to_thread(verify_password, form_data.password, password_hash)
    if not user or not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    set_access_cookie(response, create_access_token(user))
    return {"message": "Authentication successful"}


@auth_router.get("/me")
async def get_me(current_user=Depends(get_current_user)):
    return {"email": current_user.email, "id": current_user.id}


@auth_router.post("/logout", response_model=AuthStatus)
async def logout(request: Request, response: Response):
    reject_cross_site_browser_request(request)
    clear_access_cookie(response)
    return {"message": "Signed out successfully"}


@auth_router.post("/change-password", response_model=AuthStatus)
async def change_password(
    payload: PasswordChange,
    response: Response,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    password_valid = await asyncio.to_thread(
        verify_password,
        payload.current_password,
        current_user.hashed_password,
    )
    if not password_valid:
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    current_user.hashed_password = await asyncio.to_thread(hash_password, payload.new_password)
    current_user.token_version += 1
    db.commit()
    clear_access_cookie(response)
    return {"message": "Password changed successfully. Please sign in again."}
