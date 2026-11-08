"""List pagination keeps the JSON array result and fails closed."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent

import fizzy_mcp.server as server

ORIGIN = "https://app.fizzy.do"
ACCOUNT = "123"
TOKEN = "test-token"


class FakeResponse:
    def __init__(self, url, status_code=200, body=None, headers=None, json_error=False):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._body = [] if body is None else body
        self._json_error = json_error
        self.request = httpx.Request("GET", url)
        self.url = httpx.URL(url)

    def json(self):
        if self._json_error:
            raise json.JSONDecodeError("bad", "", 0)
        return self._body


class Router:
    def __init__(self):
        self.calls = []
        self.routes = {}

    def add(self, url, response):
        self.routes[url] = response
        return self

    def __call__(self, url, headers=None, params=None, follow_redirects=None, **kwargs):
        self.calls.append(
            {
                "url": str(url),
                "headers": dict(headers or {}),
                "params": params,
                "follow_redirects": follow_redirects,
            }
        )
        parsed = urlsplit(str(url))
        if parsed.scheme != "https" or parsed.hostname != "app.fizzy.do" or parsed.port not in (None, 443):
            raise AssertionError(f"request left configured origin: {url}")
        if parsed.username or parsed.password:
            raise AssertionError(f"request carried userinfo: {url}")
        auth = (headers or {}).get("Authorization")
        if auth != f"Bearer {TOKEN}":
            raise AssertionError("unexpected authorization")
        if str(url) not in self.routes:
            raise AssertionError(f"unexpected URL {url} params={params}")
        response = self.routes[str(url)]
        # httpx exposes the URL it actually requested, including params.
        requested = httpx.URL(str(url))
        if params:
            requested = requested.copy_merge_params(params)
        response.url = requested
        return response


def page(url, items, next_url=None, link=None):
    headers = {}
    if link is not None:
        headers["Link"] = link
    elif next_url is not None:
        headers["Link"] = f'<{next_url}>; rel="next"'
    return FakeResponse(url, body=items, headers=headers)


@pytest.fixture(autouse=True)
def configured_origin(monkeypatch):
    monkeypatch.setattr(server, "TOKEN", TOKEN)
    monkeypatch.setattr(server, "ACCOUNT_SLUG", ACCOUNT)
    monkeypatch.setattr(server, "BASE_URL", ORIGIN)

    def refuse(*args, **kwargs):
        raise AssertionError("unexpected HTTP entry")

    for name in ("Client", "AsyncClient", "request", "get", "post", "put", "delete"):
        monkeypatch.setattr(httpx, name, refuse)


def route_get(monkeypatch, router):
    monkeypatch.setattr(httpx, "get", router)
    return router


def test_multipage_list_returns_every_page_as_an_array(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    third = f"{ORIGIN}/{ACCOUNT}/cards?indexed_by=closed&sorted_by=oldest&page=3"
    router = Router()
    router.add(first, page(first, [{"id": "c1"}], next_url=second))
    router.add(second, page(second, [{"id": "c2"}], next_url=third))
    router.add(third, page(third, [{"id": "c3"}]))
    route_get(monkeypatch, router)

    result = server.list_cards(indexed_by="closed", sorted_by="oldest")

    assert result == [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
    assert router.calls[0]["params"]["indexed_by"] == "closed"
    assert router.calls[0]["params"]["sorted_by"] == "oldest"
    assert router.calls[0]["follow_redirects"] is False
    assert [call["url"] for call in router.calls] == [first, second, third]
    assert router.calls[1]["params"] is None
    assert router.calls[2]["params"] is None


def test_short_page_with_next_is_not_the_end(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/boards"
    second = f"{ORIGIN}/{ACCOUNT}/boards?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "only"}], next_url=second))
    router.add(second, page(second, [{"id": "rest"}]))
    route_get(monkeypatch, router)

    assert server.list_boards() == [{"id": "only"}, {"id": "rest"}]
    assert len(router.calls) == 2


@pytest.mark.parametrize("count", [8, 15])
def test_page_without_next_returns_that_page_only(monkeypatch, count):
    url = f"{ORIGIN}/{ACCOUNT}/boards"
    items = [{"id": f"b{i}"} for i in range(count)]
    router = Router()
    router.add(url, page(url, items))
    route_get(monkeypatch, router)

    assert server.list_boards() == items
    assert len(router.calls) == 1


def test_empty_page_with_next_still_fetches_the_next_page(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards/4/comments"
    second = f"{ORIGIN}/{ACCOUNT}/cards/4/comments?page=2"
    router = Router()
    router.add(first, page(first, [], next_url=second))
    router.add(second, page(second, [{"id": "comment-1"}]))
    route_get(monkeypatch, router)

    assert server.list_comments(4) == [{"id": "comment-1"}]


def test_first_request_keeps_filters_and_absolute_next_url(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = (
        f"{ORIGIN}/{ACCOUNT}/cards?indexed_by=not_now&sorted_by=latest"
        "&board_ids%5B%5D=b1&tag_ids%5B%5D=t1&assignee_ids%5B%5D=u1"
        "&terms%5B%5D=queue&creation=thisweek&closure=lastweek&page=2"
    )
    router = Router()
    router.add(first, page(first, [{"id": "n1"}], next_url=second))
    router.add(second, page(second, [{"id": "n2"}]))
    route_get(monkeypatch, router)

    result = server.list_cards(
        board_ids=["b1"],
        tag_ids=["t1"],
        assignee_ids=["u1"],
        indexed_by="not_now",
        sorted_by="latest",
        terms=["queue"],
        creation="thisweek",
        closure="lastweek",
    )

    assert result == [{"id": "n1"}, {"id": "n2"}]
    params = router.calls[0]["params"]
    assert params["indexed_by"] == "not_now"
    assert params["board_ids[]"] == ["b1"]
    assert params["tag_ids[]"] == ["t1"]
    assert params["assignee_ids[]"] == ["u1"]
    assert params["terms[]"] == ["queue"]
    assert params["creation"] == "thisweek"
    assert params["closure"] == "lastweek"
    assert router.calls[1]["url"] == second
    assert router.calls[1]["params"] is None


def test_query_only_next_keeps_the_filters_already_sent(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(first, page(first, [{"id": "n1"}], link='<?page=2>; rel="next"'))
    route_get(monkeypatch, router)

    def remember(url, headers=None, params=None, follow_redirects=None, **kwargs):
        assert (headers or {}).get("Authorization") == f"Bearer {TOKEN}"
        assert follow_redirects is False
        router.calls.append(
            {"url": str(url), "headers": dict(headers or {}), "params": params, "follow_redirects": follow_redirects}
        )
        if not router.calls or router.calls[-1]["url"] == first and len(router.calls) == 1:
            response = router.routes[first]
            requested = httpx.URL(first).copy_merge_params(params)
            response.url = requested
            return response
        query = parse_qs(urlsplit(str(url)).query)
        assert query["indexed_by"] == ["not_now"]
        assert query["page"] == ["2"]
        assert urlsplit(str(url)).path == f"/{ACCOUNT}/cards"
        return page(str(url), [{"id": "n2"}])

    monkeypatch.setattr(httpx, "get", remember)
    assert server.list_cards(indexed_by="not_now") == [{"id": "n1"}, {"id": "n2"}]


def test_root_relative_next_keeps_filters_and_account_path(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(first, page(first, [{"id": "n1"}], link=f'</{ACCOUNT}/cards?page=2>; rel="next"'))
    route_get(monkeypatch, router)

    seen = {}

    def remember(url, headers=None, params=None, follow_redirects=None, **kwargs):
        assert (headers or {}).get("Authorization") == f"Bearer {TOKEN}"
        assert follow_redirects is False
        seen.setdefault("calls", []).append({"url": str(url), "params": params, "follow_redirects": follow_redirects})
        if len(seen["calls"]) == 1:
            response = router.routes[first]
            response.url = httpx.URL(first).copy_merge_params(params)
            return response
        return page(str(url), [{"id": "n2"}])

    monkeypatch.setattr(httpx, "get", remember)
    assert server.list_cards(indexed_by="closed") == [{"id": "n1"}, {"id": "n2"}]
    second = urlsplit(seen["calls"][1]["url"])
    query = parse_qs(second.query)
    assert second.path == f"/{ACCOUNT}/cards"
    assert query["indexed_by"] == ["closed"]
    assert query["page"] == ["2"]
    assert seen["calls"][1]["params"] is None


@pytest.mark.parametrize(
    ("name", "arguments", "url"),
    [
        ("list_boards", {}, f"{ORIGIN}/{ACCOUNT}/boards"),
        ("list_columns", {"board_id": "board-1"}, f"{ORIGIN}/{ACCOUNT}/boards/board-1/columns"),
        ("list_cards", {"indexed_by": "not_now"}, f"{ORIGIN}/{ACCOUNT}/cards"),
        ("list_tags", {}, f"{ORIGIN}/{ACCOUNT}/tags"),
        ("list_comments", {"card_number": 7}, f"{ORIGIN}/{ACCOUNT}/cards/7/comments"),
        ("list_reactions", {"card_number": 7}, f"{ORIGIN}/{ACCOUNT}/cards/7/reactions"),
        ("list_users", {}, f"{ORIGIN}/{ACCOUNT}/users"),
        ("list_notifications", {}, f"{ORIGIN}/{ACCOUNT}/notifications"),
        ("list_pins", {}, f"{ORIGIN}/my/pins"),
    ],
)
def test_each_list_tool_returns_every_page(monkeypatch, name, arguments, url):
    second = f"{url}?page=2"
    router = Router()
    router.add(url, page(url, [{"id": "a"}], next_url=second))
    router.add(second, page(second, [{"id": "b"}]))
    route_get(monkeypatch, router)

    assert getattr(server, name)(**arguments) == [{"id": "a"}, {"id": "b"}]
    assert router.calls[0]["url"] == url
    if name == "list_cards":
        assert router.calls[0]["params"]["indexed_by"] == "not_now"


def test_exact_page_limit_without_another_link_returns_the_full_list(monkeypatch):
    monkeypatch.setattr(server, "MAX_LIST_PAGES", 2)
    first = f"{ORIGIN}/{ACCOUNT}/boards"
    second = f"{ORIGIN}/{ACCOUNT}/boards?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "b1"}], next_url=second))
    router.add(second, page(second, [{"id": "b2"}]))
    route_get(monkeypatch, router)

    assert server.list_boards() == [{"id": "b1"}, {"id": "b2"}]


def test_page_limit_fails_without_returning_a_partial_list(monkeypatch):
    monkeypatch.setattr(server, "MAX_LIST_PAGES", 2)
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    third = f"{ORIGIN}/{ACCOUNT}/cards?page=3"
    router = Router()
    router.add(first, page(first, [{"id": "p1"}], next_url=second))
    router.add(second, page(second, [{"id": "p2"}], next_url=third))
    router.add(third, page(third, [{"id": "p3"}]))
    route_get(monkeypatch, router)

    with pytest.raises(server.PaginationError, match="^pagination page limit$"):
        server.list_cards()
    assert [call["url"] for call in router.calls] == [first, second]


def test_repeated_next_url_fails(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/boards"
    second = f"{ORIGIN}/{ACCOUNT}/boards?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "b1"}], next_url=second))
    router.add(second, page(second, [{"id": "b2"}], next_url=first))
    route_get(monkeypatch, router)

    with pytest.raises(server.PaginationError, match="^pagination loop$"):
        server.list_boards()
    assert len(router.calls) == 2


@pytest.mark.parametrize(
    ("status", "body", "json_error", "message"),
    [
        (500, [{"id": "nope"}], False, "pagination request failed"),
        (404, [], False, "pagination request failed"),
        (200, {"cards": [{"id": "hidden"}]}, False, "pagination response was not a JSON array"),
        (200, None, True, "pagination response was not JSON"),
    ],
)
def test_bad_page_fails_the_call(monkeypatch, status, body, json_error, message):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, FakeResponse(url, status_code=status, body=body, json_error=json_error))
    route_get(monkeypatch, router)

    with pytest.raises(server.PaginationError, match=f"^{message}$"):
        server.list_cards()


def test_http_error_on_a_later_page_is_not_a_successful_prefix(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "c1"}], next_url=second))
    router.add(second, FakeResponse(second, status_code=500, body=[{"id": "nope"}]))
    route_get(monkeypatch, router)

    with pytest.raises(server.PaginationError, match="^pagination request failed$"):
        server.list_cards()


def test_malformed_next_link_is_not_a_successful_page(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, page(url, [{"id": "c1"}], link='rel="next"'))
    route_get(monkeypatch, router)

    with pytest.raises(server.PaginationError, match="^pagination link is unusable$"):
        server.list_cards()
    assert len(router.calls) == 1


def test_redirect_is_not_followed(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(
        first,
        FakeResponse(
            first,
            status_code=302,
            body=[{"id": "unfiltered"}],
            headers={"Location": "https://evil.example/cards?access_token=sekrit"},
        ),
    )
    route_get(monkeypatch, router)

    with pytest.raises(server.PaginationError, match="^pagination redirect refused$") as caught:
        server.list_cards(indexed_by="not_now")
    assert [call["url"] for call in router.calls] == [first]
    assert "sekrit" not in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert router.calls[0]["params"]["indexed_by"] == "not_now"


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/cards?page=2&access_token=sekrit",
        "http://app.fizzy.do/123/cards?page=2",
        "https://app.fizzy.do.evil.example/123/cards?page=2",
        "https://user:sekrit-userinfo@app.fizzy.do/123/cards?page=2",
        "https://app.fizzy.do:8443/123/cards?page=2",
        "https://app.fizzy.do/999/cards?page=2&access_token=sekrit",
        "//evil.example/cards?access_token=sekrit",
        "/999/cards?page=2&access_token=sekrit",
        f"/{ACCOUNT}/../999/cards?page=2",
    ],
)
def test_unsafe_next_link_is_not_requested(monkeypatch, next_url):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(first, page(first, [{"id": "c1"}], next_url=next_url))
    route_get(monkeypatch, router)

    with pytest.raises(server.PaginationError, match="^pagination link refused$") as caught:
        server.list_cards()
    assert [call["url"] for call in router.calls] == [first]
    text = str(caught.value)
    assert text == "pagination link refused"
    assert "sekrit" not in text
    assert TOKEN not in text
    assert "evil" not in text


def test_pins_stay_under_my_and_reject_an_account_path(monkeypatch):
    first = f"{ORIGIN}/my/pins"
    second = f"{ORIGIN}/my/pins?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "pin"}], next_url=second))
    router.add(second, page(second, [{"id": "pin-2"}]))
    route_get(monkeypatch, router)
    assert server.list_pins() == [{"id": "pin"}, {"id": "pin-2"}]

    refused = Router()
    refused.add(first, page(first, [{"id": "pin"}], next_url=f"{ORIGIN}/{ACCOUNT}/cards?page=2&access_token=sekrit"))
    route_get(monkeypatch, refused)
    with pytest.raises(server.PaginationError, match="^pagination link refused$"):
        server.list_pins()
    assert [call["url"] for call in refused.calls] == [first]


def test_transport_failure_uses_a_fixed_message(monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.ConnectError("https://app.fizzy.do/123/cards token=sekrit")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(server.PaginationError, match="^pagination request failed$") as caught:
        server.list_boards()
    assert "sekrit" not in str(caught.value)


def test_fastmcp_list_result_stays_text_blocks_without_an_envelope(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, page(url, [{"id": "c1", "title": "Queue"}, {"id": "c2", "title": "Next"}]))
    route_get(monkeypatch, router)
    tool = server.mcp._tool_manager.get_tool("list_cards")
    assert tool.output_schema is None
    assert "continuation" not in tool.parameters["properties"]
    assert "indexed_by" in tool.parameters["properties"]

    result = asyncio.run(server.mcp.call_tool("list_cards", {"indexed_by": "not_now"}))

    assert isinstance(result, list)
    assert result
    assert all(isinstance(block, TextContent) for block in result)
    assert [json.loads(block.text)["id"] for block in result] == ["c1", "c2"]
    assert "completeness" not in result[0].text


def test_fastmcp_pagination_failure_is_a_tool_error(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, page(url, [{"id": "c1"}], next_url="https://evil.example/x?access_token=sekrit"))
    route_get(monkeypatch, router)

    with pytest.raises(ToolError) as caught:
        asyncio.run(server.mcp.call_tool("list_cards", {}))
    assert "pagination link refused" in str(caught.value)
    assert "sekrit" not in str(caught.value)
    assert TOKEN not in str(caught.value)


def _json_response(status, body):
    class Response:
        status_code = status
        headers = {"Location": f"/{ACCOUNT}/boards/board-1"}

        def raise_for_status(self):
            return None

        def json(self):
            return body

    return Response()


def test_create_board_json_body_passes_declared_string_validation(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _json_response(201, {"id": "board-1", "name": "Queue"}))
    _blocks, structured = asyncio.run(server.mcp.call_tool("create_board", {"name": "Queue"}))
    assert isinstance(structured["result"], str)
    assert json.loads(structured["result"])["id"] == "board-1"


def test_update_board_json_body_passes_declared_string_validation(monkeypatch):
    monkeypatch.setattr(httpx, "put", lambda *args, **kwargs: _json_response(200, {"id": "board-1", "name": "Renamed"}))
    _blocks, structured = asyncio.run(server.mcp.call_tool("update_board", {"board_id": "board-1", "name": "Renamed"}))
    assert isinstance(structured["result"], str)
    assert json.loads(structured["result"])["name"] == "Renamed"


def test_no_content_write_stays_a_plain_string(monkeypatch):
    class Response:
        status_code = 204
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            raise AssertionError("204 body should not be parsed")

    monkeypatch.setattr(httpx, "put", lambda *args, **kwargs: Response())
    _blocks, structured = asyncio.run(server.mcp.call_tool("update_board", {"board_id": "board-1", "name": "Same"}))
    assert structured["result"] == "OK"
