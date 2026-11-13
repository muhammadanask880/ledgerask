from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: int
