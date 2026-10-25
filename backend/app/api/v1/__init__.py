"""Version 1 of the API. Breaking changes go in a new /api/v2 package."""

from fastapi import APIRouter

from . import admin, auth, catalog, dashboard, exams, live, questions, student, subjects

router = APIRouter(prefix="/api/v1")
for module in (auth, catalog, admin, dashboard, subjects, questions, exams, live, student):
    router.include_router(module.router)
