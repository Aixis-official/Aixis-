"""Contact form schemas."""

from pydantic import BaseModel, EmailStr
from typing import Optional


class ContactRequest(BaseModel):
    company_name: str
    department: Optional[str] = None
    name: str
    email: EmailStr
    phone: Optional[str] = None
    inquiry_type: str  # 監査依頼, 料金プラン, パートナーシップ, その他
    message: str


class ContactResponse(BaseModel):
    success: bool
    message: str
