"""Admin routes — server-side is_admin enforcement.

Provides endpoints for managing the approved-emails beta allowlist.
Every endpoint checks is_admin on the authenticated user row; non-admin
users get a 403 regardless of whether they guess or discover the URL.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app import auth, db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(current: dict) -> dict:
    """Reject non-admin users with 403. Returns the user dict if admin."""
    if not current.get("is_admin"):
        raise HTTPException(403, "Admin access required.")
    return current


class AddEmailRequest(BaseModel):
    email: EmailStr
    is_admin: bool = False


@router.post("/approved-emails")
async def add_approved_email(
    req: AddEmailRequest,
    current=Depends(auth.get_current_user),
):
    """Add an email to the beta allowlist. Idempotent — re-adding is a no-op update."""
    _require_admin(current)
    email = req.email.lower().strip()
    db.execute(
        """
        INSERT INTO approved_emails (email, is_admin, added_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (email) DO UPDATE
          SET is_admin = EXCLUDED.is_admin, added_by = EXCLUDED.added_by
        """,
        (email, req.is_admin, current["email"]),
    )
    return {"email": email, "is_admin": req.is_admin}


@router.get("/approved-emails")
async def list_approved_emails(
    current=Depends(auth.get_current_user),
):
    """List all approved emails. Admin-only."""
    _require_admin(current)
    rows = db.query_all(
        "SELECT email, is_admin, added_by, created_at "
        "FROM approved_emails ORDER BY created_at DESC"
    )
    return [
        {
            "email": r["email"],
            "is_admin": r["is_admin"],
            "added_by": r.get("added_by"),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]


@router.delete("/approved-emails/{email}")
async def remove_approved_email(
    email: str,
    current=Depends(auth.get_current_user),
):
    """Remove an email from the allowlist. Admin-only. Cannot remove yourself."""
    _require_admin(current)
    email = email.lower().strip()
    if email == current["email"].lower().strip():
        raise HTTPException(400, "Cannot remove your own email from the allowlist.")
    result = db.execute(
        "DELETE FROM approved_emails WHERE email = %s RETURNING email", (email,)
    )
    if result is None:
        raise HTTPException(404, "Email not found in allowlist.")
    return {"deleted": email}
