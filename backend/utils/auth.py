# utils/auth.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
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

# ==========================================
# SCHEMAS
# ==========================================
class UserSignUp(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class Token(BaseModel):
    access_token: str
    token_type: str

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
        if email is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
        
    user = db.query(UserModel).filter(UserModel.email == email).first()
    if user is None:
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
    
    # <-- NEW: Verify password with pure bcrypt
    if not user or not bcrypt.checkpw(form_data.password.encode('utf-8'), user.hashed_password.encode('utf-8')):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": user.email, "exp": expire}
    access_token = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    
    return {"access_token": access_token, "token_type": "bearer"}

@auth_router.get("/me")
async def get_me(current_user = Depends(get_current_user)):
    return {"email": current_user.email, "id": current_user.id}
