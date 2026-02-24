"""Fizzy MCP Server - Kanban tool by 37signals.

Environment variables:
    FIZZY_ACCESS_TOKEN: Personal access token from fizzy.do
    FIZZY_ACCOUNT_SLUG: Account slug (numeric ID from your Fizzy URL)
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://app.fizzy.do"
TOKEN = os.environ.get("FIZZY_ACCESS_TOKEN", "")
ACCOUNT_SLUG = os.environ.get("FIZZY_ACCOUNT_SLUG", "")

mcp = FastMCP(
    "fizzy",
    instructions=(
        "Fizzy is a Kanban board by 37signals. "
        "Use these tools to manage boards, cards, columns, tags, comments, steps and more."
    ),
)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    return f"{BASE_URL}/{ACCOUNT_SLUG}{path}"


def _get(path: str, params: dict | None = None):
    r = httpx.get(_url(path), headers=_headers(), params=params, follow_redirects=True)
    r.raise_for_status()
    if r.status_code == 204:
        return "OK"
    return r.json()


def _post(path: str, data: dict | None = None):
    r = httpx.post(_url(path), headers=_headers(), json=data, follow_redirects=True)
    r.raise_for_status()
    location = r.headers.get("Location", "")
    if r.status_code == 201:
        try:
            return r.json()
        except Exception:
            return f"Created. Location: {location}"
    if r.status_code == 204:
        return "OK"
    try:
        return r.json()
    except Exception:
        return f"OK ({r.status_code})"


def _put(path: str, data: dict | None = None):
    r = httpx.put(_url(path), headers=_headers(), json=data, follow_redirects=True)
    r.raise_for_status()
    if r.status_code == 204:
        return "OK"
    try:
        return r.json()
    except Exception:
        return f"OK ({r.status_code})"


def _delete(path: str):
    r = httpx.delete(_url(path), headers=_headers(), follow_redirects=True)
    r.raise_for_status()
    return "Deleted"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

@mcp.tool()
def get_identity() -> dict:
    """Get the current user's identity and list of Fizzy accounts."""
    r = httpx.get(f"{BASE_URL}/my/identity", headers=_headers(), follow_redirects=True)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------

@mcp.tool()
def list_boards() -> list:
    """List all boards you have access to."""
    return _get("/boards")


@mcp.tool()
def get_board(board_id: str) -> dict:
    """Get a specific board.

    Args:
        board_id: The board ID.
    """
    return _get(f"/boards/{board_id}")


@mcp.tool()
def create_board(name: str, all_access: bool = True) -> str:
    """Create a new board.

    Args:
        name: Board name.
        all_access: Whether any user in the account can access it. Defaults to True.
    """
    return _post("/boards", {"board": {"name": name, "all_access": all_access}})


@mcp.tool()
def update_board(
    board_id: str,
    name: str | None = None,
    all_access: bool | None = None,
    auto_postpone_period: int | None = None,
) -> str:
    """Update a board.

    Args:
        board_id: The board ID.
        name: New board name.
        all_access: Whether any user can access this board.
        auto_postpone_period: Days of inactivity before cards are auto-postponed.
    """
    data: dict = {}
    if name is not None:
        data["name"] = name
    if all_access is not None:
        data["all_access"] = all_access
    if auto_postpone_period is not None:
        data["auto_postpone_period"] = auto_postpone_period
    return _put(f"/boards/{board_id}", {"board": data})


@mcp.tool()
def delete_board(board_id: str) -> str:
    """Delete a board. Only board administrators can do this.

    Args:
        board_id: The board ID.
    """
    return _delete(f"/boards/{board_id}")


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

@mcp.tool()
def list_columns(board_id: str) -> list:
    """List columns on a board, sorted by position.

    Args:
        board_id: The board ID.
    """
    return _get(f"/boards/{board_id}/columns")


@mcp.tool()
def create_column(
    board_id: str,
    name: str,
    color: str = "var(--color-card-default)",
) -> str:
    """Create a new column on a board.

    Args:
        board_id: The board ID.
        name: Column name.
        color: Column color CSS variable. Options: var(--color-card-default) Blue,
               var(--color-card-1) Gray, var(--color-card-2) Tan, var(--color-card-3) Yellow,
               var(--color-card-4) Lime, var(--color-card-5) Aqua, var(--color-card-6) Violet,
               var(--color-card-7) Purple, var(--color-card-8) Pink.
    """
    return _post(f"/boards/{board_id}/columns", {"column": {"name": name, "color": color}})


@mcp.tool()
def update_column(
    board_id: str,
    column_id: str,
    name: str | None = None,
    color: str | None = None,
) -> str:
    """Update a column's name or color.

    Args:
        board_id: The board ID.
        column_id: The column ID.
        name: New column name.
        color: New column color CSS variable.
    """
    data: dict = {}
    if name is not None:
        data["name"] = name
    if color is not None:
        data["color"] = color
    return _put(f"/boards/{board_id}/columns/{column_id}", {"column": data})


@mcp.tool()
def delete_column(board_id: str, column_id: str) -> str:
    """Delete a column from a board.

    Args:
        board_id: The board ID.
        column_id: The column ID.
    """
    return _delete(f"/boards/{board_id}/columns/{column_id}")


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

@mcp.tool()
def list_cards(
    board_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    assignee_ids: list[str] | None = None,
    indexed_by: str = "all",
    sorted_by: str = "latest",
    terms: list[str] | None = None,
    creation: str | None = None,
    closure: str | None = None,
) -> list:
    """List cards with optional filters.

    Args:
        board_ids: Filter by board ID(s).
        tag_ids: Filter by tag ID(s).
        assignee_ids: Filter by assignee user ID(s).
        indexed_by: Filter type - all, closed, not_now, stalled, postponing_soon, golden.
        sorted_by: Sort order - latest, newest, oldest.
        terms: Search terms to filter cards.
        creation: Filter by creation period - today, yesterday, thisweek, lastweek,
                  thismonth, lastmonth, thisyear, lastyear.
        closure: Filter by closure period - same options as creation.
    """
    params: dict = {"indexed_by": indexed_by, "sorted_by": sorted_by}
    if board_ids:
        params["board_ids[]"] = board_ids
    if tag_ids:
        params["tag_ids[]"] = tag_ids
    if assignee_ids:
        params["assignee_ids[]"] = assignee_ids
    if terms:
        params["terms[]"] = terms
    if creation:
        params["creation"] = creation
    if closure:
        params["closure"] = closure
    return _get("/cards", params)


@mcp.tool()
def get_card(card_number: int) -> dict:
    """Get a specific card by its number.

    Args:
        card_number: The card number (visible in Fizzy UI).
    """
    return _get(f"/cards/{card_number}")


@mcp.tool()
def create_card(
    board_id: str,
    title: str,
    description: str = "",
    status: str = "published",
    tag_ids: list[str] | None = None,
) -> str:
    """Create a new card on a board.

    Args:
        board_id: The board ID.
        title: Card title.
        description: Card description (supports HTML rich text).
        status: Initial status - published (default) or drafted.
        tag_ids: Tag IDs to apply to the card.
    """
    card: dict = {"title": title, "status": status}
    if description:
        card["description"] = description
    if tag_ids:
        card["tag_ids"] = tag_ids
    return _post(f"/boards/{board_id}/cards", {"card": card})


@mcp.tool()
def update_card(
    card_number: int,
    title: str | None = None,
    description: str | None = None,
    tag_ids: list[str] | None = None,
) -> str:
    """Update a card.

    Args:
        card_number: The card number.
        title: New title.
        description: New description (supports HTML rich text).
        tag_ids: New set of tag IDs.
    """
    card: dict = {}
    if title is not None:
        card["title"] = title
    if description is not None:
        card["description"] = description
    if tag_ids is not None:
        card["tag_ids"] = tag_ids
    return _put(f"/cards/{card_number}", {"card": card})


@mcp.tool()
def delete_card(card_number: int) -> str:
    """Delete a card.

    Args:
        card_number: The card number.
    """
    return _delete(f"/cards/{card_number}")


@mcp.tool()
def close_card(card_number: int) -> str:
    """Close a card (mark as done).

    Args:
        card_number: The card number.
    """
    return _post(f"/cards/{card_number}/closure")


@mcp.tool()
def reopen_card(card_number: int) -> str:
    """Reopen a closed card.

    Args:
        card_number: The card number.
    """
    return _delete(f"/cards/{card_number}/closure")


@mcp.tool()
def triage_card(card_number: int, column_id: str) -> str:
    """Move a card from triage into a column.

    Args:
        card_number: The card number.
        column_id: Target column ID.
    """
    return _post(f"/cards/{card_number}/triage", {"column_id": column_id})


@mcp.tool()
def untriage_card(card_number: int) -> str:
    """Send a card back to triage.

    Args:
        card_number: The card number.
    """
    return _delete(f"/cards/{card_number}/triage")


@mcp.tool()
def postpone_card(card_number: int) -> str:
    """Move a card to Not Now.

    Args:
        card_number: The card number.
    """
    return _post(f"/cards/{card_number}/not_now")


@mcp.tool()
def mark_golden(card_number: int) -> str:
    """Mark a card as golden (starred).

    Args:
        card_number: The card number.
    """
    return _post(f"/cards/{card_number}/goldness")


@mcp.tool()
def unmark_golden(card_number: int) -> str:
    """Remove golden status from a card.

    Args:
        card_number: The card number.
    """
    return _delete(f"/cards/{card_number}/goldness")


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@mcp.tool()
def list_tags() -> list:
    """List all tags in the account."""
    return _get("/tags")


@mcp.tool()
def toggle_tag(card_number: int, tag_title: str) -> str:
    """Toggle a tag on/off for a card. Creates the tag if it doesn't exist.

    Args:
        card_number: The card number.
        tag_title: Tag name (leading # is stripped automatically).
    """
    return _post(f"/cards/{card_number}/taggings", {"tag_title": tag_title})


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

@mcp.tool()
def toggle_assignment(card_number: int, assignee_id: str) -> str:
    """Toggle a user's assignment to/from a card.

    Args:
        card_number: The card number.
        assignee_id: User ID to assign/unassign.
    """
    return _post(f"/cards/{card_number}/assignments", {"assignee_id": assignee_id})


@mcp.tool()
def self_assign(card_number: int) -> str:
    """Assign yourself to a card.

    Args:
        card_number: The card number.
    """
    return _post(f"/cards/{card_number}/self_assignment")


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@mcp.tool()
def list_comments(card_number: int) -> list:
    """List comments on a card (oldest first).

    Args:
        card_number: The card number.
    """
    return _get(f"/cards/{card_number}/comments")


@mcp.tool()
def get_comment(card_number: int, comment_id: str) -> dict:
    """Get a specific comment.

    Args:
        card_number: The card number.
        comment_id: The comment ID.
    """
    return _get(f"/cards/{card_number}/comments/{comment_id}")


@mcp.tool()
def create_comment(card_number: int, body: str) -> str:
    """Add a comment to a card.

    Args:
        card_number: The card number.
        body: Comment body (supports HTML rich text).
    """
    return _post(f"/cards/{card_number}/comments", {"comment": {"body": body}})


@mcp.tool()
def update_comment(card_number: int, comment_id: str, body: str) -> str:
    """Update a comment. Only the comment creator can update it.

    Args:
        card_number: The card number.
        comment_id: The comment ID.
        body: Updated comment body.
    """
    return _put(f"/cards/{card_number}/comments/{comment_id}", {"comment": {"body": body}})


@mcp.tool()
def delete_comment(card_number: int, comment_id: str) -> str:
    """Delete a comment. Only the comment creator can delete it.

    Args:
        card_number: The card number.
        comment_id: The comment ID.
    """
    return _delete(f"/cards/{card_number}/comments/{comment_id}")


# ---------------------------------------------------------------------------
# Reactions (Boosts)
# ---------------------------------------------------------------------------

@mcp.tool()
def list_reactions(card_number: int) -> list:
    """List reactions (boosts) on a card.

    Args:
        card_number: The card number.
    """
    return _get(f"/cards/{card_number}/reactions")


@mcp.tool()
def add_reaction(card_number: int, content: str) -> str:
    """Add a reaction (boost) to a card. Max 16 characters.

    Args:
        card_number: The card number.
        content: Reaction text (max 16 chars, e.g. an emoji).
    """
    return _post(f"/cards/{card_number}/reactions", {"reaction": {"content": content}})


@mcp.tool()
def remove_reaction(card_number: int, reaction_id: str) -> str:
    """Remove your reaction from a card.

    Args:
        card_number: The card number.
        reaction_id: The reaction ID.
    """
    return _delete(f"/cards/{card_number}/reactions/{reaction_id}")


# ---------------------------------------------------------------------------
# Steps (to-do items on cards)
# ---------------------------------------------------------------------------

@mcp.tool()
def create_step(card_number: int, content: str) -> str:
    """Add a step (to-do item) to a card.

    Args:
        card_number: The card number.
        content: Step text.
    """
    return _post(f"/cards/{card_number}/steps", {"step": {"content": content}})


@mcp.tool()
def update_step(
    card_number: int,
    step_id: str,
    content: str | None = None,
    completed: bool | None = None,
) -> str:
    """Update a step's content or completion status.

    Args:
        card_number: The card number.
        step_id: The step ID.
        content: New step text.
        completed: Whether the step is done.
    """
    data: dict = {}
    if content is not None:
        data["content"] = content
    if completed is not None:
        data["completed"] = completed
    return _put(f"/cards/{card_number}/steps/{step_id}", {"step": data})


@mcp.tool()
def delete_step(card_number: int, step_id: str) -> str:
    """Delete a step from a card.

    Args:
        card_number: The card number.
        step_id: The step ID.
    """
    return _delete(f"/cards/{card_number}/steps/{step_id}")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@mcp.tool()
def list_users() -> list:
    """List all active users in the account."""
    return _get("/users")


@mcp.tool()
def get_user(user_id: str) -> dict:
    """Get a specific user.

    Args:
        user_id: The user ID.
    """
    return _get(f"/users/{user_id}")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@mcp.tool()
def list_notifications() -> list:
    """List notifications for the current user. Unread first."""
    return _get("/notifications")


@mcp.tool()
def mark_notification_read(notification_id: str) -> str:
    """Mark a notification as read.

    Args:
        notification_id: The notification ID.
    """
    return _post(f"/notifications/{notification_id}/reading")


@mcp.tool()
def mark_notification_unread(notification_id: str) -> str:
    """Mark a notification as unread.

    Args:
        notification_id: The notification ID.
    """
    return _delete(f"/notifications/{notification_id}/reading")


@mcp.tool()
def mark_all_notifications_read() -> str:
    """Mark all notifications as read."""
    return _post("/notifications/bulk_reading")


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------

@mcp.tool()
def list_pins() -> list:
    """List pinned cards for the current user (up to 100)."""
    r = httpx.get(f"{BASE_URL}/my/pins", headers=_headers(), follow_redirects=True)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def pin_card(card_number: int) -> str:
    """Pin a card for quick access.

    Args:
        card_number: The card number.
    """
    return _post(f"/cards/{card_number}/pin")


@mcp.tool()
def unpin_card(card_number: int) -> str:
    """Unpin a card.

    Args:
        card_number: The card number.
    """
    return _delete(f"/cards/{card_number}/pin")


# ---------------------------------------------------------------------------
# Watch
# ---------------------------------------------------------------------------

@mcp.tool()
def watch_card(card_number: int) -> str:
    """Subscribe to notifications for a card.

    Args:
        card_number: The card number.
    """
    return _post(f"/cards/{card_number}/watch")


@mcp.tool()
def unwatch_card(card_number: int) -> str:
    """Unsubscribe from notifications for a card.

    Args:
        card_number: The card number.
    """
    return _delete(f"/cards/{card_number}/watch")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
