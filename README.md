# fizzy-mcp

MCP server for [Fizzy](https://fizzy.do) — the Kanban tool by [37signals](https://37signals.com).

Lets AI assistants (Claude, etc.) read and manage your Fizzy boards, cards, columns, tags, comments and more.

## Quick start

### 1. Get your Fizzy access token

Go to your Fizzy profile → **Developer** → **Personal access tokens** → **Generate new access token** with **Read + Write** permission.

Find your **account slug** — it's the number in your Fizzy URL: `app.fizzy.do/<account_slug>/boards`.

### 2. Configure

#### Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "fizzy": {
      "command": "uvx",
      "args": ["fizzy-mcp"],
      "env": {
        "FIZZY_ACCESS_TOKEN": "your-token-here",
        "FIZZY_ACCOUNT_SLUG": "your-slug-here"
      }
    }
  }
}
```

#### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "fizzy": {
      "command": "uvx",
      "args": ["fizzy-mcp"],
      "env": {
        "FIZZY_ACCESS_TOKEN": "your-token-here",
        "FIZZY_ACCOUNT_SLUG": "your-slug-here"
      }
    }
  }
}
```

## Available tools

### Boards
| Tool | Description |
|------|-------------|
| `list_boards` | List all boards |
| `get_board` | Get a specific board |
| `create_board` | Create a new board |
| `update_board` | Update board name, access, auto-postpone |
| `delete_board` | Delete a board |

### Columns
| Tool | Description |
|------|-------------|
| `list_columns` | List columns on a board |
| `create_column` | Create a column with name and color |
| `update_column` | Update column name or color |
| `delete_column` | Delete a column |

### Cards
| Tool | Description |
|------|-------------|
| `list_cards` | List cards with filters (board, tags, assignees, status, dates, search) |
| `get_card` | Get a card by number |
| `create_card` | Create a card with title, description (HTML), tags |
| `update_card` | Update card title, description, tags |
| `delete_card` | Delete a card |
| `close_card` | Mark a card as done |
| `reopen_card` | Reopen a closed card |
| `triage_card` | Move a card into a column |
| `untriage_card` | Send a card back to triage |
| `postpone_card` | Move a card to Not Now |
| `mark_golden` | Star a card |
| `unmark_golden` | Unstar a card |

### Tags
| Tool | Description |
|------|-------------|
| `list_tags` | List all tags |
| `toggle_tag` | Toggle a tag on/off for a card |

### Assignments
| Tool | Description |
|------|-------------|
| `toggle_assignment` | Assign/unassign a user to a card |
| `self_assign` | Assign yourself to a card |

### Comments
| Tool | Description |
|------|-------------|
| `list_comments` | List comments on a card |
| `get_comment` | Get a specific comment |
| `create_comment` | Add a comment (supports HTML) |
| `update_comment` | Edit a comment |
| `delete_comment` | Delete a comment |

### Reactions
| Tool | Description |
|------|-------------|
| `list_reactions` | List reactions on a card |
| `add_reaction` | Add a reaction/boost (max 16 chars) |
| `remove_reaction` | Remove a reaction |

### Steps (to-dos)
| Tool | Description |
|------|-------------|
| `create_step` | Add a to-do step to a card |
| `update_step` | Update step text or mark complete |
| `delete_step` | Delete a step |

### Users
| Tool | Description |
|------|-------------|
| `list_users` | List active users |
| `get_user` | Get a specific user |

### Notifications
| Tool | Description |
|------|-------------|
| `list_notifications` | List your notifications |
| `mark_notification_read` | Mark one as read |
| `mark_notification_unread` | Mark one as unread |
| `mark_all_notifications_read` | Mark all as read |

### Pins & Watch
| Tool | Description |
|------|-------------|
| `list_pins` | List your pinned cards |
| `pin_card` / `unpin_card` | Pin or unpin a card |
| `watch_card` / `unwatch_card` | Subscribe/unsubscribe from card notifications |

### Identity
| Tool | Description |
|------|-------------|
| `get_identity` | Get current user and accounts |

## Development

```bash
git clone https://github.com/alexroubinet/fizzy-mcp.git
cd fizzy-mcp
pip install -e .
FIZZY_ACCESS_TOKEN=xxx FIZZY_ACCOUNT_SLUG=123 fizzy-mcp
```

## License

MIT
