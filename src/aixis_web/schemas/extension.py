"""Request/response schemas for the Chrome extension API."""

from pydantic import BaseModel, Field


class ExtensionSessionCreate(BaseModel):
    tool_id: str
    profile_id: str = ""
    recording_mode: str = "protocol"  # "protocol" | "freeform"
    categories: list[str] | None = None
    max_cases: int = Field(30, ge=1, le=100)  # Max test cases (manual testing)


class TestCaseOut(BaseModel):
    id: str
    category: str
    prompt: str
    expected_behaviors: list[str] = Field(default_factory=list)
    failure_indicators: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ExtensionSessionResponse(BaseModel):
    session_id: str
    session_code: str
    tool_id: str
    status: str
    recording_mode: str
    test_cases: list[TestCaseOut] = Field(default_factory=list)


class ObservationUpload(BaseModel):
    test_case_id: str | None = None  # protocol: test case ID, freeform: None
    prompt_text: str
    response_text: str | None = None
    response_time_ms: int = 0
    page_url: str | None = None
    screenshot_base64: str | None = None  # Base64-encoded PNG screenshot
    metadata: dict = Field(default_factory=dict)


class ObservationResponse(BaseModel):
    observation_id: int
    sequence_number: int
    screenshot_saved: bool = False  # Whether screenshot was saved to disk


class SessionProgressResponse(BaseModel):
    session_id: str
    session_code: str
    status: str
    recording_mode: str
    total_planned: int
    total_executed: int
    completeness_ratio: int


class ToolListItem(BaseModel):
    id: str
    name: str
    name_jp: str
    vendor: str = ""
    category_name_jp: str = ""
