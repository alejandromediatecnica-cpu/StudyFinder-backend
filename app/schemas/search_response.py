from typing import List

from pydantic import BaseModel

from models.document import Document


class SearchResponse(BaseModel):
    results: List[Document]