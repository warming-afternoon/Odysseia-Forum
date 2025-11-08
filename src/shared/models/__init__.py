from .tag import Tag
from .thread import Thread
from .thread_tag_link import ThreadTagLink
from .tag_vote import TagVote
from .user_search_preferences import UserSearchPreferences
from .mutex_tag_group import MutexTagGroup
from .mutex_tag_rule import MutexTagRule
from .bot_config import BotConfig
from .author import Author
from .thread_follow import ThreadFollow
from .banner_application import BannerApplication, BannerCarousel, BannerWaitlist

# 这一行是为了让 Alembic/SQLModel 能够发现所有模型
__all__ = [
    "Tag",
    "Thread",
    "ThreadTagLink",
    "TagVote",
    "UserSearchPreferences",
    "MutexTagGroup",
    "MutexTagRule",
    "BotConfig",
    "Author",
    "ThreadFollow",
    "BannerApplication",
    "BannerCarousel",
    "BannerWaitlist",
]
