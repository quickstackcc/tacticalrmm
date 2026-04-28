from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    token: str = Field(..., min_length=1, description="action_id from the pending Redis row")


class RejectRequest(BaseModel):
    token: str = Field(..., min_length=1)
    reason: str = Field(default="", max_length=500)


class ActionResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    error: str
