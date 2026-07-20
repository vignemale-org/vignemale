"""The `catalog` service alone — for service-to-service call tests (HTTP)."""

from vignemale import Service, api, auth_handler

catalog = Service("catalog")


@auth_handler
def check_token(token):
    if token == "sesame":
        return {"user_id": "u-42"}
    return None


@api(method="GET", path="/items/:id", auth=True)
def get_item(id, auth):
    return {"id": int(id), "name": "widget", "seen_user": auth["user_id"]}


if __name__ == "__main__":
    import os

    from vignemale import serve

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
