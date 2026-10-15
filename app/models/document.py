from pydantic import BaseModel


class Document(BaseModel):
    title: str
    author: str
    abstract: str
    url: str