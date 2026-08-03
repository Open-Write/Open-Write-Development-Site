"""Authentication routes: signup, login, current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app import auth, db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/signup", response_model=TokenResponse)
async def signup(req: SignupRequest):
    email = req.email.lower().strip()
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    existing = db.query_one("SELECT id FROM users WHERE email = %s", (email,))
    if existing:
        raise HTTPException(409, "An account with this email already exists.")
    # Beta gating: check approved_emails before creating account.
    approved = db.query_one(
        "SELECT is_admin FROM approved_emails WHERE email = %s", (email,)
    )
    if not approved:
        raise HTTPException(
            403,
            "This beta is invite-only. Contact open.write.studio@gmail.com for access.",
        )
    row = db.execute(
        "INSERT INTO users (email, password_hash, is_admin) VALUES (%s, %s, %s) "
        "RETURNING id, email, is_admin, created_at",
        (email, auth.hash_password(req.password), approved["is_admin"]),
    )
    user_id = str(row["id"])
    token = auth.create_access_token(user_id, email)
    return TokenResponse(
        access_token=token,
        user={"id": user_id, "email": email, "is_admin": row["is_admin"]},
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    email = req.email.lower().strip()
    user = db.query_one(
        "SELECT id, email, password_hash, is_admin FROM users WHERE email = %s", (email,)
    )
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password.")
    user_id = str(user["id"])
    token = auth.create_access_token(user_id, email)
    return TokenResponse(
        access_token=token,
        user={"id": user_id, "email": email, "is_admin": user["is_admin"]},
    )


@router.get("/me")
async def me(current=Depends(auth.get_current_user)):
    return {
        "id": str(current["id"]),
        "email": current["email"],
        "is_admin": current.get("is_admin", False),
        "created_at": current["created_at"].isoformat()
        if current.get("created_at") else None,
    }
