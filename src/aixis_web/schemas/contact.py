"""Contact form schemas with input validation."""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional


ALLOWED_INQUIRY_TYPES = {
    "監査データベース利用に関するご相談",
    "クイック診断のご相談",
    "個別監査レポートのご相談",
    "ツール掲載に関するご相談",
    "パートナーシップに関するご相談",
    "その他のご相談",
    # Legacy values (backward compatibility)
    "料金プランのご相談",
    "無料トライアルのご相談",
    "クイック診断",
    "料金プラン",
    "個別レポート",
    "掲載依頼",
    "パートナーシップ",
    "その他",
}


class ContactRequest(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=200)
    department: Optional[str] = Field(None, max_length=200)
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=30, pattern=r'^[\d\-\+\(\)\s]*$')
    inquiry_type: str = Field(..., min_length=1, max_length=50)
    message: str = Field(..., min_length=1, max_length=5000)


class ContactResponse(BaseModel):
    success: bool
    message: str
