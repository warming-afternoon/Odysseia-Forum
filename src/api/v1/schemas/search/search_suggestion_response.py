from typing import List

from pydantic import BaseModel, Field

from api.v1.schemas.search.author_suggestion import AuthorSuggestion
from api.v1.schemas.search.booklist_suggestion import BooklistSuggestion
from api.v1.schemas.search.thread_suggestion import ThreadSuggestion


class SearchSuggestionResponse(BaseModel):
    """全局搜索建议的完整响应体"""

    authors: List[AuthorSuggestion] = Field(
        default_factory=list, description="匹配的作者 (最多3个)"
    )
    threads: List[ThreadSuggestion] = Field(
        default_factory=list, description="匹配的帖子 (最多3个)"
    )
    booklists: List[BooklistSuggestion] = Field(
        default_factory=list, description="匹配的书单 (最多3个)"
    )
