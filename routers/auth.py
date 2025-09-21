from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models import Client, Admin
from schemas import ClientCreate, ClientResponse, AdminCreate, AdminResponse, LoginRequest, Token
from auth_utils import verify_password, get_password_hash, create_access_token, get_current_user
from datetime import timedelta
from config import settings

router = APIRouter()

@router.post("/register/client", response_model=ClientResponse)
async def register_client(client: ClientCreate, db: Session = Depends(get_db)):
    """Register a new client."""
    # Check if user already exists
    if db.query(Client).filter(Client.email == client.email).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    if db.query(Client).filter(Client.username == client.username).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )
    
    # Create new client
    hashed_password = get_password_hash(client.password)
    db_client = Client(
        email=client.email,
        username=client.username,
        hashed_password=hashed_password,
        full_name=client.full_name,
        organization=client.organization
    )
    
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    
    return db_client

@router.post("/register/admin", response_model=AdminResponse)
async def register_admin(admin: AdminCreate, db: Session = Depends(get_db)):
    """Admin registration is disabled - admin credentials are hardcoded."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin registration is disabled. Admin credentials are pre-configured."
    )

@router.post("/login", response_model=Token)
async def login(login_data: LoginRequest, db: Session = Depends(get_db)):
    """Login for both clients and admins."""
    # Try to find user in clients table
    user = db.query(Client).filter(Client.username == login_data.username).first()
    user_role = "client"
    
    # If not found in clients, try admins table
    if not user:
        user = db.query(Admin).filter(Admin.username == login_data.username).first()
        user_role = "admin"
        
        # Admin login restriction - only allow specific username and password
        if user_role == "admin":
            if login_data.username != "Thejeshwini" or login_data.password != "Theju@#$123":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Admin access denied. Invalid credentials.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
    
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user.username, "role": user_role}, 
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me")
async def get_current_user_info(current_user = Depends(get_current_user)):
    """Get current user information."""
    return current_user
