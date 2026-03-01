"""
Authentication Router - Login, Register, JWT handling
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from database import get_db, User, init_db
from schemas import UserRegister, UserLogin, UserResponse, TokenResponse
from config import settings

router = APIRouter()
security = HTTPBearer()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = credentials.credentials
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if user is None:
        raise credentials_exception
    return user

@router.on_event("startup")
async def startup():
    await init_db()

@router.post("/register", response_model=TokenResponse)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user"""
    # Check if phone already exists
    result = await db.execute(select(User).where(User.phone == user_data.phone))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        phone=user_data.phone,
        name=user_data.name,
        password_hash=hashed_password,
        user_type=user_data.user_type,
        language=user_data.language,
        location=user_data.location
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Create access token
    access_token = create_access_token(data={"sub": new_user.id})
    
    return TokenResponse(
        access_token=access_token,
        user=UserResponse(
            id=new_user.id,
            phone=new_user.phone,
            name=new_user.name,
            user_type=new_user.user_type,
            language=new_user.language,
            location=new_user.location,
            created_at=new_user.created_at
        )
    )

@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """Login with phone and password"""
    # Find user by phone
    result = await db.execute(select(User).where(User.phone == credentials.phone))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password"
        )
    
    # Create access token
    access_token = create_access_token(data={"sub": user.id})
    
    return TokenResponse(
        access_token=access_token,
        user=UserResponse(
            id=user.id,
            phone=user.phone,
            name=user.name,
            user_type=user.user_type,
            language=user.language,
            location=user.location,
            created_at=user.created_at
        )
    )

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return UserResponse(
        id=current_user.id,
        phone=current_user.phone,
        name=current_user.name,
        user_type=current_user.user_type,
        language=current_user.language,
        location=current_user.location,
        created_at=current_user.created_at
    )

@router.post("/logout")
async def logout():
    """Logout user (client should discard token)"""
    return {"message": "Logged out successfully"}
