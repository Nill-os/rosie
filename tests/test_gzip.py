from fastapi.testclient import TestClient

import server


def test_large_static_file_is_gzipped():
    c = TestClient(server.app)
    r = c.get("/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    print("PASS: large static file is gzipped")


def test_event_stream_is_not_compressed():
    from starlette.responses import StreamingResponse

    async def gen():
        yield b"data: " + b"x" * 5000 + b"\n\n"

    def probe():
        return StreamingResponse(gen(), media_type="text/event-stream")

    server.app.add_api_route("/__sse_probe", probe)
    route = server.app.router.routes.pop()
    server.app.router.routes.insert(0, route)
    try:
        r = TestClient(server.app).get("/__sse_probe", headers={"Accept-Encoding": "gzip"})
    finally:
        server.app.router.routes.remove(route)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") is None
    print("PASS: event stream is not compressed")
