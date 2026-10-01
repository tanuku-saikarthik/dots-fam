"""Space pages: the team's shared source of truth. Pages keep revisions, so edits are reversible."""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..context import DotContext


class SpaceArg(BaseModel):
    space: str | None = Field(None, description="Space name or id. Defaults to your default Space.")


class ReadPage(BaseModel):
    page: str = Field(description="Page title or id.")
    space: str | None = Field(None, description="Space name or id. Defaults to your default Space.")


class CreatePage(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field("", max_length=100_000, description="Markdown.")
    space: str | None = Field(None, description="Space name or id. Defaults to your default Space.")
    parent: str | None = Field(None, description="Optional parent page title or id.")


class UpdatePage(BaseModel):
    page: str = Field(description="Page title or id.")
    expected_revision: int = Field(description="Revision you last read; stale writes are refused.")
    content: str | None = Field(None, max_length=100_000, description="Full new Markdown content.")
    title: str | None = Field(None, max_length=160)
    space: str | None = Field(None, description="Space name or id. Defaults to your default Space.")


def page_tools(ctx: DotContext) -> list[BaseTool]:
    store, dot = ctx.store, ctx.dot

    def resolve_space(value: str | None) -> dict:
        if not value:
            space = store.space(dot["space_id"])
        else:
            space = next(
                (
                    s
                    for s in store.spaces()
                    if s["id"] == value or s["name"].lower() == value.lower()
                ),
                None,
            )
            if not space:
                raise ValueError(f"No Space named {value}.")
        if not store.can_access_space(dot["id"], space["id"]):
            raise PermissionError(f"{dot['name']} has no access to the {space['name']} Space.")
        return space

    def resolve_page(space: dict, value: str) -> dict:
        page = None
        try:
            page = store.page(value)
        except LookupError:
            page = store.page_by_title(space["id"], value)
        if not page or page["space_id"] != space["id"]:
            raise ValueError(f'No page "{value}" in {space["name"]}.')
        return page

    async def list_spaces() -> list[dict]:
        ctx.check()
        return [
            {"id": s["id"], "name": s["name"], "description": s["description"]}
            for s in store.spaces()
            if store.can_access_space(dot["id"], s["id"])
        ]

    async def list_pages(space: str | None = None) -> dict:
        ctx.check()
        target = resolve_space(space)
        return {
            "space": target["name"],
            "pages": [
                {
                    "id": p["id"],
                    "title": p["title"],
                    "revision": p["revision"],
                    "updated_at": p["updated_at"],
                }
                for p in store.pages(target["id"])
            ],
        }

    async def read_page(page: str, space: str | None = None) -> dict:
        ctx.check()
        target = resolve_space(space)
        found = resolve_page(target, page)
        return {
            "id": found["id"],
            "title": found["title"],
            "revision": found["revision"],
            "content": found["content"],
            "space": target["name"],
        }

    async def create_page(
        title: str, content: str = "", space: str | None = None, parent: str | None = None
    ) -> dict:
        ctx.check()
        target = resolve_space(space)
        parent_id = resolve_page(target, parent)["id"] if parent else None
        page = store.create_page(target["id"], title, content, parent_id, author=dot["name"])
        ctx.log(f'Created page "{title}" in {target["name"]}', "page")
        return {
            "id": page["id"],
            "title": page["title"],
            "revision": page["revision"],
            "link": f"/spaces/{target['id']}/pages/{page['id']}",
        }

    async def update_page(
        page: str,
        expected_revision: int,
        content: str | None = None,
        title: str | None = None,
        space: str | None = None,
    ) -> dict:
        ctx.check()
        target = resolve_space(space)
        found = resolve_page(target, page)
        updated = store.update_page(
            found["id"],
            expected_revision=expected_revision,
            title=title,
            content=content,
            author=dot["name"],
        )
        ctx.log(f'Updated page "{updated["title"]}" to revision {updated["revision"]}', "page")
        return {
            "id": updated["id"],
            "title": updated["title"],
            "revision": updated["revision"],
            "link": f"/spaces/{target['id']}/pages/{updated['id']}",
        }

    return [
        StructuredTool.from_function(
            coroutine=list_spaces,
            name="list_spaces",
            description="List the Spaces you can read and write.",
        ),
        StructuredTool.from_function(
            coroutine=list_pages,
            name="list_pages",
            args_schema=SpaceArg,
            description="List pages in a Space (titles, ids, revisions).",
        ),
        StructuredTool.from_function(
            coroutine=read_page,
            name="read_page",
            args_schema=ReadPage,
            description="Read a page's Markdown and its current revision.",
        ),
        StructuredTool.from_function(
            coroutine=create_page,
            name="create_page",
            args_schema=CreatePage,
            description="Create a Markdown page in a Space. Use for deliverables the team should keep.",
        ),
        StructuredTool.from_function(
            coroutine=update_page,
            name="update_page",
            args_schema=UpdatePage,
            description="Replace a page's content (or title). Read it first and pass expected_revision.",
        ),
    ]
