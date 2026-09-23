# utils/auth.py
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator
import jwt
import bcrypt  # <-- NEW: Using native bcrypt instead of passlib
from sqlalchemy.orm import Session

from config import settings
from utils.database import get_db
from models.user import UserModel

# ==========================================
# SETUP
# ==========================================
auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")
DUMMY_PASSWORD_HASH = "$2b$12$s6Fa5Ej.V25qiExVU69cBePDkTowiSR1UnVUXcaTmeqGJOCEoCA/e"


def validate_password_strength(value: str) -> str:
    if not any(char.islower() for char in value) or not any(char.isupper() for char in value) or not any(char.isdigit() for char in value):
        raise ValueError("Password must include uppercase, lowercase, and numeric characters.")
    return value

# ==========================================
# SCHEMAS
# ==========================================
class UserSignUp(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        return validate_password_strength(value)

class Token(BaseModel):
    access_token: str
    token_type: str


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def strong_new_password(cls, value: str) -> str:
        return validate_password_strength(value)

# ==========================================
# DEPENDENCY
# ==========================================
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    if settings.AUTH_PROVIDER == "supabase":
        try:
            payload = jwt.decode(
                token, 
                settings.SUPABASE_JWT_SECRET, 
                algorithms=[settings.JWT_ALGORITHM],
                audience="authenticated"
            )
            class SupabaseUser:
                id = payload.get("sub")
                email = payload.get("email")
            return SupabaseUser()
        except jwt.PyJWTError:
            raise credentials_exception

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        email: str = payload.get("sub")
        token_version = payload.get("ver")
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
        
    user = db.query(UserModel).filter(UserModel.email == email).first()
    if user is None or token_version != user.token_version:
        raise credentials_exception
    return user

# ==========================================
# ROUTES
# ==========================================
@auth_router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserSignUp, db: Session = Depends(get_db)):
    if settings.AUTH_PROVIDER == "supabase":
        raise HTTPException(status_code=400, detail="Signup disabled. Use Supabase UI.")
        
    if db.query(UserModel).filter(UserModel.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # <-- NEW: Hash password with pure bcrypt
    salt = bcrypt.gensalt()
    hashed_pass = bcrypt.hashpw(user_data.password.encode('utf-8'), salt).decode('utf-8')
    
    new_user = UserModel(email=user_data.email, hashed_password=hashed_pass)
    db.add(new_user)
    db.commit()
    return {"message": "User registered successfully"}

@auth_router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    if settings.AUTH_PROVIDER == "supabase":
        raise HTTPException(status_code=400, detail="Login disabled. Use Supabase UI.")

    user = db.query(UserModel).filter(UserModel.email == form_data.username).first()
    password_hash = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_valid = bcrypt.checkpw(form_data.password.encode('utf-8'), password_hash.encode('utf-8'))
    if not user or not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": user.email, "ver": user.token_version, "exp": expire}
    access_token = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    
    return {"access_token": access_token, "token_type": "bearer"}

@auth_router.get("/me")
async def get_me(current_user = Depends(get_current_user)):
    return {"email": current_user.email, "id": current_user.id}


@auth_router.post("/change-password")
async def change_password(
    payload: PasswordChange,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if settings.AUTH_PROVIDER == "supabase":
        raise HTTPException(status_code=400, detail="Password changes must be handled by Supabase auth.")

    user = db.query(UserModel).filter(UserModel.id == current_user.id).first()
    if not user or not bcrypt.checkpw(payload.current_password.encode("utf-8"), user.hashed_password.encode("utf-8")):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    salt = bcrypt.gensalt()
    user.hashed_password = bcrypt.hashpw(payload.new_password.encode("utf-8"), salt).decode("utf-8")
    user.token_version += 1
    db.commit()
    return {"message": "Password changed successfully. Sign in again on all devices."}


@auth_router.post("/logout-all")
async def logout_all(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    if settings.AUTH_PROVIDER == "supabase":
        raise HTTPException(status_code=400, detail="Session revocation must be handled by Supabase auth.")
    user = db.query(UserModel).filter(UserModel.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    user.token_version += 1
    db.commit()
    return {"message": "All sessions were revoked."}
