from .admin_cmds import router as admin_router
from .counting import router as counting_router
from .games import router as games_router
from .quiz import router as quiz_router
from .user_cmds import router as user_router

__all__ = [
    "admin_router",
    "quiz_router",
    "user_router",
    "games_router",
    "counting_router",
]
