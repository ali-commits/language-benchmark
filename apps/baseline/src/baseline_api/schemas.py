from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.lower()


class Login(Credentials):
    password: str = Field(min_length=1, max_length=128)


class User(BaseModel):
    id: int
    username: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class PostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=280)

    @field_validator("content")
    @classmethod
    def nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v


class CommentInput(PostInput):
    content: str = Field(min_length=1, max_length=1000)


class Comment(BaseModel):
    id: int
    content: str
    created_at: datetime
    author: User


class Post(Comment):
    like_count: int
    comment_count: int
    liked: bool


class PostPage(BaseModel):
    items: list[Post]
    next_cursor: str | None


class CommentPage(BaseModel):
    items: list[Comment]
    next_cursor: str | None
