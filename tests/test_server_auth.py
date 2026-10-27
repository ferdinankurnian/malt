import asyncio

from malt.server import _TokenAuthMiddleware


def _request(path, *, headers=(), method="GET", query=b""):
    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": list(headers),
    }
    asyncio.run(_TokenAuthMiddleware(downstream, {"p1": "secret"})(scope, receive, send))
    return messages, seen


def test_auth_rejects_unknown_and_wrong_tokens():
    unknown, _ = _request("/mcp/nope")
    wrong, _ = _request("/mcp/p1", query=b"token=wrong")
    assert unknown[0]["status"] == 404
    assert wrong[0]["status"] == 401


def test_auth_accepts_bearer_and_canonicalizes_path_token():
    messages, seen = _request(
        "/mcp/p1/secret",
        headers=((b"authorization", b"Bearer secret"),),
    )
    assert messages[0]["status"] == 200
    assert seen == ["/mcp/p1"]


def test_options_is_preflight():
    messages, _ = _request("/mcp/p1", method="OPTIONS")
    assert messages[0]["status"] == 204
