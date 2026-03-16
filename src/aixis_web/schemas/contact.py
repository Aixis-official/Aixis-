"""Contact form schemas with input validation."""

from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional


ALLOWED_INQUIRY_TYPES = {
    "監査データベース利用（無料トライアル）に関するご相談",
    "クイック診断のご相談",
    "個別監査レポートのご相談",
    "ツール掲載に関するご相談",
    "導入支援・カスタム監査のご相談",
    "その他のご相談",
    # Legacy values (backward compatibility)
    "パートナーシップに関するご相談",
    "監査データベース利用に関するご相談",
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

    @field_validator("inquiry_type")
    @classmethod
    def validate_inquiry_type(cls, v: str) -> str:
        if v not in ALLOWED_INQUIRY_TYPES:
            raise ValueError(
                f"無効なお問い合わせ種別です。許可された値: {', '.join(sorted(ALLOWED_INQUIRY_TYPES))}"
            )
        return v


class ContactResponse(BaseModel):
    success: bool
    message: str
