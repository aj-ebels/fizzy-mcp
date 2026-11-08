"""List pagination and write-result compatibility.

Fizzy list endpoints are variable-sized. A short page is complete only when
the response has no Link rel=next. These tests use a fake HTTP transport.
"""

import asyncio
import json
from urllib.parse import urlsplit

import httpx
import pytest

import fizzy_mcp.server as server

ORIGIN = "https://app.fizzy.do"
ACCOUNT = "123"
TOKEN = "test-token"


class FakeResponse:
    def __init__(self, url, status_code=200, body=None, headers=None, json_error=False):
        self.status_code = status_code
        self.url = httpx.URL(url)
        self.headers = httpx.Headers(headers or {})
        self._body = [] if body is None else body
        self._json_error = json_error
        self.request = httpx.Request("GET", url)

    def json(self):
        if self._json_error:
            raise json.JSONDecodeError("bad", "", 0)
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=self,
            )


class Router:
    """Records GET/POST/PUT and refuses any request outside the configured origin."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def add(self, url, response):
        self.routes[url] = response
        return self

    def __call__(self, url, headers=None, params=None, follow_redirects=None, **kwargs):
        if len(self.calls) > 40:
            raise AssertionError("request budget exceeded")
        self.calls.append(
            {
                "url": str(url),
                "headers": dict(headers or {}),
                "params": params,
                "follow_redirects": follow_redirects,
                "method": kwargs.get("method", "GET"),
            }
        )
        parsed = urlsplit(str(url))
        if parsed.scheme != "https" or parsed.hostname != "app.fizzy.do" or parsed.port not in (None, 443):
            raise AssertionError(f"request left configured origin: {url}")
        if parsed.username or parsed.password:
            raise AssertionError(f"request carried userinfo: {url}")
        auth = (headers or {}).get("Authorization")
        if auth != f"Bearer {TOKEN}":
            raise AssertionError(f"unexpected authorization header on {url}")
        if str(url) not in self.routes:
            raise AssertionError(f"unexpected URL {url} params={params}")
        return self.routes[str(url)]


def page(url, items, next_url=None, link=None, status_code=200, json_error=False):
    headers = {}
    if link is not None:
        headers["Link"] = link
    elif next_url is not None:
        headers["Link"] = f'<{next_url}>; rel="next"'
    return FakeResponse(url, status_code=status_code, body=items, headers=headers, json_error=json_error)


def ids(result):
    return [item["id"] for item in result["items"]]


@pytest.fixture(autouse=True)
def configured_origin(monkeypatch):
    monkeypatch.setattr(server, "TOKEN", TOKEN)
    monkeypatch.setattr(server, "ACCOUNT_SLUG", ACCOUNT)
    monkeypatch.setattr(server, "BASE_URL", ORIGIN)

    def refuse(*args, **kwargs):
        raise AssertionError(f"unexpected HTTP entry: {args} {kwargs}")

    monkeypatch.setattr(httpx, "Client", refuse)
    monkeypatch.setattr(httpx, "AsyncClient", refuse)
    monkeypatch.setattr(httpx, "request", refuse)
    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "put", refuse)
    monkeypatch.setattr(httpx, "delete", refuse)
    return monkeypatch


def route_get(monkeypatch, router):
    monkeypatch.setattr(httpx, "get", router)
    return router


def test_multipage_list_is_complete_only_after_the_final_page(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    third = f"{ORIGIN}/{ACCOUNT}/cards?page=3"
    router = Router()
    router.add(first, page(first, [{"id": "c1"}, {"id": "c2"}], next_url=second))
    router.add(second, page(second, [{"id": "c3"}], next_url=third))
    router.add(third, page(third, [{"id": "c4"}, {"id": "c5"}]))
    route_get(monkeypatch, router)

    result = server.list_cards(indexed_by="closed", sorted_by="oldest")

    assert result["completeness"] == "complete"
    assert result["next"] is None
    assert result["error"] is None
    assert ids(result) == ["c1", "c2", "c3", "c4", "c5"]
    assert [call["url"] for call in router.calls] == [first, second, third]
    assert router.calls[0]["params"]["indexed_by"] == "closed"
    assert router.calls[0]["params"]["sorted_by"] == "oldest"
    assert router.calls[1]["params"] in (None, {})
    assert router.calls[2]["params"] in (None, {})
    assert all(call["follow_redirects"] is False for call in router.calls)


def test_short_page_with_next_is_not_the_end(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?indexed_by=all&page=2"
    router = Router()
    router.add(first, page(first, [{"id": "only"}], next_url=second))
    router.add(second, page(second, [{"id": "rest-1"}, {"id": "rest-2"}]))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert len(router.calls) == 2
    assert result["completeness"] == "complete"
    assert ids(result) == ["only", "rest-1", "rest-2"]


@pytest.mark.parametrize("count", [8, 15])
def test_page_without_next_is_complete_regardless_of_length(monkeypatch, count):
    url = f"{ORIGIN}/{ACCOUNT}/boards"
    items = [{"id": f"b{i}"} for i in range(count)]
    router = Router()
    router.add(url, page(url, items))
    route_get(monkeypatch, router)

    result = server.list_boards()

    assert result["completeness"] == "complete"
    assert result["next"] is None
    assert ids(result) == [item["id"] for item in items]
    assert len(router.calls) == 1


def test_empty_page_with_next_still_fetches_the_next_page(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards/4/comments"
    second = f"{ORIGIN}/{ACCOUNT}/cards/4/comments?page=2"
    router = Router()
    router.add(first, page(first, [], next_url=second))
    router.add(second, page(second, [{"id": "comment-1"}]))
    route_get(monkeypatch, router)

    result = server.list_comments(4)

    assert result["completeness"] == "complete"
    assert ids(result) == ["comment-1"]
    assert [call["url"] for call in router.calls] == [first, second]


def test_filters_survive_on_the_server_next_link(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = (
        f"{ORIGIN}/{ACCOUNT}/cards?indexed_by=not_now&sorted_by=latest"
        "&board_ids%5B%5D=b1&tag_ids%5B%5D=t1&assignee_ids%5B%5D=u1"
        "&terms%5B%5D=queue&creation=thisweek&page=2"
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

    assert result["completeness"] == "complete"
    assert ids(result) == ["n1", "n2"]
    params = router.calls[0]["params"]
    assert params["indexed_by"] == "not_now"
    assert params["sorted_by"] == "latest"
    assert params["board_ids[]"] == ["b1"]
    assert params["tag_ids[]"] == ["t1"]
    assert params["assignee_ids[]"] == ["u1"]
    assert params["terms[]"] == ["queue"]
    assert params["creation"] == "thisweek"
    assert params["closure"] == "lastweek"
    assert router.calls[1]["url"] == second
    assert router.calls[1]["params"] in (None, {})


@pytest.mark.parametrize(
    "indexed_by",
    ["all", "closed", "not_now", "stalled", "postponing_soon", "golden"],
)
def test_existing_card_status_filters_are_sent(monkeypatch, indexed_by):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, page(url, [{"id": "c"}]))
    route_get(monkeypatch, router)

    result = server.list_cards(indexed_by=indexed_by)

    assert result["completeness"] == "complete"
    assert router.calls[0]["params"]["indexed_by"] == indexed_by


def test_page_limit_returns_truncated_continuation_without_another_fetch(monkeypatch):
    monkeypatch.setattr(server, "MAX_PAGES", 2, raising=False)
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    third = f"{ORIGIN}/{ACCOUNT}/cards?page=3"
    router = Router()
    router.add(first, page(first, [{"id": "p1"}], next_url=second))
    router.add(second, page(second, [{"id": "p2"}], next_url=third))
    router.add(third, page(third, [{"id": "p3"}]))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "truncated"
    assert result["error"] is None
    assert result["next"] == third
    assert ids(result) == ["p1", "p2"]
    assert [call["url"] for call in router.calls] == [first, second]


def test_exact_page_limit_without_further_next_is_complete(monkeypatch):
    monkeypatch.setattr(server, "MAX_PAGES", 2, raising=False)
    first = f"{ORIGIN}/{ACCOUNT}/boards"
    second = f"{ORIGIN}/{ACCOUNT}/boards?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "b1"}], next_url=second))
    router.add(second, page(second, [{"id": "b2"}]))
    route_get(monkeypatch, router)

    result = server.list_boards()

    assert result["completeness"] == "complete"
    assert result["next"] is None
    assert ids(result) == ["b1", "b2"]


def test_continuation_resumes_a_truncated_read(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards?page=3"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=4"
    router = Router()
    router.add(first, page(first, [{"id": "later"}], next_url=second))
    router.add(second, page(second, [{"id": "last"}]))
    route_get(monkeypatch, router)

    result = server.list_cards(indexed_by="not_now", continuation=first)

    assert result["completeness"] == "complete"
    assert ids(result) == ["later", "last"]
    assert router.calls[0]["url"] == first
    assert router.calls[0]["params"] in (None, {})
    assert router.calls[1]["url"] == second


def test_relative_next_link_stays_on_the_configured_origin(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/tags"
    second = f"{ORIGIN}/{ACCOUNT}/tags?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "t1"}], link='</123/tags?page=2>; rel="next"'))
    router.add(second, page(second, [{"id": "t2"}]))
    route_get(monkeypatch, router)

    result = server.list_tags()

    assert result["completeness"] == "complete"
    assert ids(result) == ["t1", "t2"]
    assert router.calls[1]["url"] == second


def test_next_is_chosen_when_prev_is_also_present(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/users"
    previous = f"{ORIGIN}/{ACCOUNT}/users?page=1"
    nxt = f"{ORIGIN}/{ACCOUNT}/users?page=2"
    router = Router()
    router.add(
        first,
        page(
            first,
            [{"id": "u1"}],
            link=f'<{previous}>; rel="prev", <{nxt}>; rel="next"',
        ),
    )
    router.add(nxt, page(nxt, [{"id": "u2"}]))
    route_get(monkeypatch, router)

    result = server.list_users()

    assert ids(result) == ["u1", "u2"]
    assert [call["url"] for call in router.calls] == [first, nxt]


def test_unquoted_and_uppercase_rel_next_are_followed(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/notifications"
    second = f"{ORIGIN}/{ACCOUNT}/notifications?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "n1"}], link=f"<{second}>; rel=NEXT"))
    router.add(second, page(second, [{"id": "n2"}]))
    route_get(monkeypatch, router)

    result = server.list_notifications()

    assert result["completeness"] == "complete"
    assert ids(result) == ["n1", "n2"]


def test_repeated_link_headers_are_followed(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards/9/reactions"
    second = f"{ORIGIN}/{ACCOUNT}/cards/9/reactions?page=2"
    router = Router()
    response = page(first, [{"id": "r1"}])
    response.headers = httpx.Headers(
        [
            ("link", f'<{first}?page=0>; rel="prev"'),
            ("link", f'<{second}>; rel="next"'),
        ]
    )
    router.add(first, response)
    router.add(second, page(second, [{"id": "r2"}]))
    route_get(monkeypatch, router)

    result = server.list_reactions(9)

    assert result["completeness"] == "complete"
    assert ids(result) == ["r1", "r2"]


def test_next_url_loop_is_an_error_and_not_complete(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/boards"
    second = f"{ORIGIN}/{ACCOUNT}/boards?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "b1"}], next_url=second))
    router.add(second, page(second, [{"id": "b2"}], next_url=first))
    route_get(monkeypatch, router)

    result = server.list_boards()

    assert result["completeness"] == "error"
    assert "loop" in result["error"]
    assert result["next"] is None
    assert ids(result) == ["b1", "b2"]
    assert len(router.calls) == 2


def test_duplicate_record_stops_the_read_before_later_pages(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    third = f"{ORIGIN}/{ACCOUNT}/cards?page=3"
    router = Router()
    router.add(first, page(first, [{"id": "c1"}, {"id": "c2"}], next_url=second))
    router.add(second, page(second, [{"id": "c2"}, {"id": "c3"}], next_url=third))
    router.add(third, page(third, [{"id": "c4"}]))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "error"
    assert "duplicate" in result["error"]
    assert result["next"] is None
    assert ids(result) == ["c1", "c2"]
    assert [call["url"] for call in router.calls] == [first, second]


def test_http_error_on_a_later_page_is_not_a_complete_prefix(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    router = Router()
    router.add(first, page(first, [{"id": "c1"}], next_url=second))
    router.add(second, page(second, [{"id": "nope"}], status_code=500))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "error"
    assert "500" in result["error"]
    assert ids(result) == ["c1"]
    assert result["next"] is None


def test_non_json_and_non_array_pages_are_errors(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, page(url, {"cards": [{"id": "hidden"}]}, json_error=False))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "error"
    assert result["items"] == []
    assert "array" in result["error"]

    bad = Router()
    bad.add(url, page(url, None, json_error=True))
    route_get(monkeypatch, bad)
    failed = server.list_cards()
    assert failed["completeness"] == "error"
    assert failed["items"] == []
    assert "JSON" in failed["error"]


def test_first_page_http_error_is_not_an_empty_complete_list(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/boards"
    router = Router()
    router.add(url, page(url, [], status_code=404))
    route_get(monkeypatch, router)

    result = server.list_boards()

    assert result["completeness"] == "error"
    assert result["items"] == []
    assert "404" in result["error"]


def test_transport_failure_is_an_error_signal(monkeypatch):
    def boom(url, headers=None, params=None, follow_redirects=None, **kwargs):
        raise httpx.ConnectError("name resolution failed")

    monkeypatch.setattr(httpx, "get", boom)

    result = server.list_boards()

    assert result["completeness"] == "error"
    assert result["items"] == []
    assert "ConnectError" in result["error"]
    assert TOKEN not in result["error"]


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example/cards?page=2&access_token=sekrit",
        "http://app.fizzy.do/123/cards?page=2",
        "https://app.fizzy.do.evil.example/123/cards?page=2",
        "https://user:sekrit-userinfo@app.fizzy.do/123/cards?page=2",
        "https://app.fizzy.do:8443/123/cards?page=2",
        "https://app.fizzy.do@evil.example/123/cards?page=2",
        "//evil.example/cards?access_token=sekrit",
    ],
)
def test_off_origin_next_link_is_not_requested(monkeypatch, next_url):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(first, page(first, [{"id": "c1"}], next_url=next_url))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "error"
    assert result["next"] is None
    assert ids(result) == ["c1"]
    assert [call["url"] for call in router.calls] == [first]
    rendered = json.dumps(result)
    assert "sekrit" not in rendered
    assert TOKEN not in rendered


def test_malformed_next_link_is_not_treated_as_complete(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, page(url, [{"id": "c1"}], link='rel="next"'))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "error"
    assert ids(result) == ["c1"]
    assert len(router.calls) == 1


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
def test_same_origin_redirect_is_followed_and_keeps_the_token(monkeypatch, status_code):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    second = f"{ORIGIN}/{ACCOUNT}/cards?page=2"
    router = Router()
    router.add(
        first,
        FakeResponse(
            first,
            status_code=status_code,
            body=[{"id": "do-not-use-redirect-body"}],
            headers={"Location": second},
        ),
    )
    router.add(second, page(second, [{"id": "landed"}]))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "complete"
    assert ids(result) == ["landed"]
    assert [call["url"] for call in router.calls] == [first, second]
    assert router.calls[1]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_relative_redirect_stays_on_origin(monkeypatch):
    first = f"{ORIGIN}/{ACCOUNT}/boards"
    second = f"{ORIGIN}/{ACCOUNT}/boards?page=2"
    router = Router()
    router.add(
        first,
        FakeResponse(first, status_code=302, headers={"Location": f"/{ACCOUNT}/boards?page=2"}),
    )
    router.add(second, page(second, [{"id": "b2"}]))
    route_get(monkeypatch, router)

    result = server.list_boards()

    assert result["completeness"] == "complete"
    assert ids(result) == ["b2"]
    assert router.calls[1]["url"] == second


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/steal?access_token=sekrit",
        "http://app.fizzy.do/123/cards?page=2",
        "https://app.fizzy.do.evil.example/next",
        "//evil.example/next?access_token=sekrit",
        "https://app.fizzy.do:8443/next",
    ],
)
def test_off_origin_redirect_is_not_requested(monkeypatch, location):
    first = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(first, FakeResponse(first, status_code=302, headers={"Location": location}))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "error"
    assert result["items"] == []
    assert result["next"] is None
    assert [call["url"] for call in router.calls] == [first]
    rendered = json.dumps(result)
    assert "sekrit" not in rendered
    assert TOKEN not in rendered


def test_redirect_loop_stops(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, FakeResponse(url, status_code=302, headers={"Location": url}))
    route_get(monkeypatch, router)

    result = server.list_cards()

    assert result["completeness"] == "error"
    assert "redirect" in result["error"]
    assert len(router.calls) <= 8
    assert result["items"] == []


def test_off_origin_continuation_is_not_requested(monkeypatch):
    def refuse_get(*args, **kwargs):
        raise AssertionError("get was called")

    monkeypatch.setattr(httpx, "get", refuse_get)

    result = server.list_comments(1, continuation="https://evil.example/comments?access_token=sekrit")

    assert result["completeness"] == "error"
    assert result["items"] == []
    assert "sekrit" not in json.dumps(result)


def test_blank_continuation_uses_the_normal_first_page(monkeypatch):
    url = f"{ORIGIN}/my/pins"
    router = Router()
    router.add(url, page(url, [{"id": "pin-1"}]))
    route_get(monkeypatch, router)

    result = server.list_pins(continuation="  ")

    assert result["completeness"] == "complete"
    assert ids(result) == ["pin-1"]
    assert router.calls[0]["url"] == url


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
def test_each_list_tool_walks_to_the_last_page(monkeypatch, name, arguments, url):
    second = f"{url}?page=2"
    router = Router()
    router.add(url, page(url, [{"id": "a"}], next_url=second))
    router.add(second, page(second, [{"id": "b"}]))
    route_get(monkeypatch, router)

    result = getattr(server, name)(**arguments)

    assert result["completeness"] == "complete"
    assert ids(result) == ["a", "b"]
    assert router.calls[0]["url"] == url
    assert router.calls[1]["url"] == second
    if name == "list_cards":
        assert router.calls[0]["params"]["indexed_by"] == "not_now"


def test_list_output_schema_exposes_completeness():
    tool = server.mcp._tool_manager.get_tool("list_cards")
    schema = tool.output_schema
    assert schema is not None
    assert set(schema["properties"]) >= {"items", "completeness", "next", "error"}
    assert "indexed_by" in tool.parameters["properties"]
    assert "continuation" in tool.parameters["properties"]
    assert "indexed_by" not in tool.parameters.get("required", [])
    assert "continuation" not in tool.parameters.get("required", [])


def test_list_cards_tool_call_validates_the_envelope(monkeypatch):
    url = f"{ORIGIN}/{ACCOUNT}/cards"
    router = Router()
    router.add(url, page(url, [{"id": "c1", "title": "Queue"}]))
    route_get(monkeypatch, router)

    result = asyncio.run(server.mcp.call_tool("list_cards", {"indexed_by": "not_now"}))

    _blocks, structured = result
    assert structured["completeness"] == "complete"
    assert structured["items"][0]["id"] == "c1"
    assert structured["next"] is None


def _created_response():
    class Response:
        status_code = 201
        headers = {"Location": f"/{ACCOUNT}/boards/board-1"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "board-1", "name": "Queue"}

    return Response()


def test_create_board_json_body_passes_declared_string_validation(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: _created_response())

    result = asyncio.run(server.mcp.call_tool("create_board", {"name": "Queue"}))

    _blocks, structured = result
    assert isinstance(structured["result"], str)
    assert json.loads(structured["result"])["id"] == "board-1"


def test_update_board_json_body_passes_declared_string_validation(monkeypatch):
    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "board-1", "name": "Renamed"}

    monkeypatch.setattr(httpx, "put", lambda *args, **kwargs: Response())

    result = asyncio.run(server.mcp.call_tool("update_board", {"board_id": "board-1", "name": "Renamed"}))

    _blocks, structured = result
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

    result = asyncio.run(server.mcp.call_tool("update_board", {"board_id": "board-1", "name": "Same"}))

    _blocks, structured = result
    assert structured["result"] == "OK"
