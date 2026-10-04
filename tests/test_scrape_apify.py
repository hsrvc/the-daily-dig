"""Tests for the Apify-failure detection added to scrape_apify.py.

Specifically guards against the 2026-05-22 incident: Apify run reports
SUCCEEDED but the crawler failed every request and emits per-page error
stubs into the dataset. The pipeline used to silently treat these as posts.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scrape_apify import (  # noqa: E402
    assert_actor_succeeded,
    is_error_stub,
    partition_posts,
)


# --- is_error_stub --------------------------------------------------------


def test_error_stub_is_detected():
    stub = {
        "url": "https://www.facebook.com/uourecords",
        "error": "no_items",
        "errorDescription": "Empty or private data for provided input",
    }
    assert is_error_stub(stub) is True


def test_real_post_is_not_stub():
    real = {
        "url": "https://www.facebook.com/x/posts/pfbid0abc",
        "postId": "1561845415951235",
        "pageName": "beethobearrecords",
        "text": "新到貨",
        "error": None,
    }
    assert is_error_stub(real) is False


def test_post_without_error_field_is_not_stub():
    assert is_error_stub({"postId": "123", "url": "https://x"}) is False


def test_empty_string_error_is_not_stub():
    # `error: ""` is falsy; only truthy error strings count as stubs
    assert is_error_stub({"url": "https://x", "error": ""}) is False


# --- partition_posts ------------------------------------------------------


def test_partition_separates_real_and_stubs():
    posts = [
        {"postId": "1", "url": "https://x/posts/1"},
        {"url": "https://x", "error": "no_items"},
        {"postId": "2", "url": "https://x/posts/2"},
    ]
    real, stubs = partition_posts(posts)
    assert len(real) == 2
    assert len(stubs) == 1
    assert real[0]["postId"] == "1"
    assert stubs[0]["error"] == "no_items"


def test_partition_all_stubs():
    posts = [
        {"url": "https://x", "error": "no_items"},
        {"url": "https://y", "error": "no_items"},
    ]
    real, stubs = partition_posts(posts)
    assert real == []
    assert len(stubs) == 2


def test_partition_empty():
    assert partition_posts([]) == ([], [])


# --- assert_actor_succeeded -----------------------------------------------


def test_assert_succeeded_passes_on_healthy_run():
    run = {
        "id": "abc",
        "status": "SUCCEEDED",
        "statusMessage": None,
        "defaultDatasetId": "ds1",
    }
    # No raise, no exit
    assert_actor_succeeded(run)


def test_assert_succeeded_passes_when_message_missing():
    assert_actor_succeeded({"id": "abc", "status": "SUCCEEDED"})


def test_assert_succeeded_exits_on_all_failed_message():
    # Exact statusMessage observed on the 2026-05-22 run
    run = {
        "id": "abc",
        "status": "SUCCEEDED",
        "statusMessage": "Finished! Total 4 requests: 0 succeeded, 4 failed.",
        "defaultDatasetId": "xyz",
    }
    with pytest.raises(SystemExit) as exc:
        assert_actor_succeeded(run)
    assert exc.value.code == 1


def test_assert_succeeded_passes_when_some_finished():
    # "0 succeeded" is the specific marker; a partial-success message shouldn't trip it
    run = {
        "id": "abc",
        "status": "SUCCEEDED",
        "statusMessage": "Finished! Total 4 requests: 3 succeeded, 1 failed.",
    }
    assert_actor_succeeded(run)
