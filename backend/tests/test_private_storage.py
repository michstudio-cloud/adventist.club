"""Private evidence bucket: keys, limits and presigned URLs. Nothing here touches the network:
presigning is a local computation and every object call goes to a fake client."""

import uuid
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from botocore.exceptions import ClientError

from app.config import settings
from app.services import private_storage

MB = 1024 * 1024


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET_NAME", "evidence-test")


def test_allowed_types_kinds_and_size_caps():
    assert set(private_storage.ALLOWED_TYPES) == {"image/jpeg", "image/png", "image/webp", "application/pdf"}
    assert private_storage.kind_for("image/webp") == "image"
    assert private_storage.kind_for("application/pdf") == "pdf"
    assert private_storage.max_size_for("image/jpeg") == 10 * MB
    assert private_storage.max_size_for("application/pdf") == 20 * MB
    with pytest.raises(ValueError):
        private_storage.kind_for("image/gif")


def test_key_is_built_from_ids_and_the_validated_mime_type():
    user, enrollment, evidence = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = private_storage.build_key(user, enrollment, evidence, "image/jpeg")
    assert key == f"evidence/{user}/{enrollment}/{evidence}.jpg"
    assert private_storage.build_key(user, enrollment, evidence, "application/pdf").endswith(".pdf")
    with pytest.raises(ValueError):
        private_storage.build_key(user, enrollment, evidence, "text/html")


def test_not_configured_without_the_private_bucket(monkeypatch):
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET_NAME", None)
    assert settings.private_storage_configured is False
    with pytest.raises(private_storage.PrivateStorageNotConfigured):
        private_storage.presign_get("evidence/a/b/c.jpg")


def test_presigned_put_binds_type_and_length(configured, monkeypatch):
    """A real boto3 client with dummy credentials: signing is offline."""
    import boto3

    client = boto3.client("s3", endpoint_url="https://acct.r2.cloudflarestorage.com",
                          aws_access_key_id="k", aws_secret_access_key="s", region_name="auto")
    monkeypatch.setattr(private_storage, "get_client", lambda: client)

    upload = private_storage.presign_put("evidence/u/e/x.jpg", "image/jpeg", 1234)
    assert upload["method"] == "PUT" and upload["expires_in"] == 600
    assert upload["headers"] == {"Content-Type": "image/jpeg"}
    url = urlsplit(upload["url"])
    query = parse_qs(url.query)
    assert url.path == "/evidence-test/evidence/u/e/x.jpg"          # the private bucket, never the public one
    assert query["X-Amz-Expires"] == ["600"]
    assert {"content-length", "content-type", "host"} <= set(unquote(query["X-Amz-SignedHeaders"][0]).split(";"))

    read = private_storage.presign_get("evidence/u/e/x.jpg")
    assert read["expires_in"] == 300 and parse_qs(urlsplit(read["url"]).query)["X-Amz-Expires"] == ["300"]


async def test_head_and_delete_use_the_private_bucket(configured, monkeypatch):
    calls = []

    class Fake:
        def head_object(self, Bucket, Key):
            calls.append(("head", Bucket, Key))
            if Key.endswith("missing.jpg"):
                raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
            return {"ContentLength": 99, "ContentType": "image/png"}

        def delete_object(self, Bucket, Key):
            calls.append(("delete", Bucket, Key))

    monkeypatch.setattr(private_storage, "get_client", lambda: Fake())
    found = await private_storage.head("evidence/u/e/x.png")
    assert (found.size_bytes, found.content_type) == (99, "image/png")
    assert await private_storage.head("evidence/u/e/missing.jpg") is None
    await private_storage.delete("evidence/u/e/x.png")
    assert calls[-1] == ("delete", "evidence-test", "evidence/u/e/x.png")
    assert {bucket for _, bucket, _ in calls} == {"evidence-test"}
