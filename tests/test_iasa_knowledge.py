from unittest.mock import Mock

import pytest

from agent.knowledge import chunk_text, search_iasa_knowledge, upsert_chunks
from agent.ingest_iasa import extract_text, upload_source_file


def test_chunk_text_splits_with_overlap():
    assert chunk_text("abcdefghij", chunk_size=6, overlap=2) == ["abcdef", "efghij"]


def test_chunk_text_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=5, overlap=5)


def test_upload_source_file_preserves_relative_key_and_returns_key(tmp_path):
    source = tmp_path / "sub" / "guide.txt"
    source.parent.mkdir()
    source.write_text("guide text", encoding="utf-8")
    s3 = Mock()

    key = upload_source_file(s3, "project1-dev-iasa-kb", tmp_path, source)

    assert key == "source/iasa/sub/guide.txt"
    s3.put_object.assert_called_once()
    request = s3.put_object.call_args.kwargs
    assert request["Bucket"] == "project1-dev-iasa-kb"
    assert request["Key"] == key
    assert request["Body"] == b"guide text"


def test_extract_plain_text_and_json(tmp_path):
    text_file = tmp_path / "readme.txt"
    text_file.write_text("hello text", encoding="utf-8")
    json_file = tmp_path / "record.json"
    json_file.write_text('{"title":"A","body":"B"}', encoding="utf-8")

    assert extract_text(text_file) == "hello text"
    assert "title" in extract_text(json_file)


def test_extract_image_calls_textract_with_uploaded_object():
    textract = Mock()
    textract.detect_document_text.return_value = {
        "Blocks": [
            {"BlockType": "LINE", "Text": "visible text"},
            {"BlockType": "WORD", "Text": "ignored word"},
        ]
    }

    result = extract_text("diagram.png", bucket="source-bucket", key="source/iasa/diagram.png", textract_client=textract)

    assert result == "visible text"
    textract.detect_document_text.assert_called_once_with(
        Document={"S3Object": {"Bucket": "source-bucket", "Name": "source/iasa/diagram.png"}}
    )


def test_upsert_chunks_writes_titan_vectors_and_source_metadata():
    vectors = Mock()
    embed = Mock(return_value=[0.25] * 1024)

    upsert_chunks(vectors, "vectors", "iasa-index", "source/iasa/guide.txt", ["chunk one", "chunk two"], embed)

    request = vectors.put_vectors.call_args.kwargs
    assert request["vectorBucketName"] == "vectors"
    assert request["indexName"] == "iasa-index"
    assert len(request["vectors"]) == 2
    assert request["vectors"][0]["data"]["float32"] == [0.25] * 1024
    assert request["vectors"][0]["metadata"]["source_key"] == "source/iasa/guide.txt"
    assert embed.call_count == 2


def test_search_embeds_query_and_returns_matches_with_sources():
    embed = Mock(return_value=[0.5] * 1024)
    vectors = Mock()
    vectors.query_vectors.return_value = {
        "vectors": [
            {"distance": 0.12, "metadata": {"text": "matched passage", "source_key": "source/iasa/a.pdf"}},
        ]
    }

    result = search_iasa_knowledge(vectors, "vectors", "iasa-index", "question", embed)

    assert "matched passage" in result
    assert "source/iasa/a.pdf" in result
    vectors.query_vectors.assert_called_once()
    assert vectors.query_vectors.call_args.kwargs["topK"] == 5
    assert vectors.query_vectors.call_args.kwargs["returnMetadata"] is True


def test_unsupported_file_fails_explicitly(tmp_path):
    path = tmp_path / "binary.bin"
    path.write_bytes(b"data")

    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(path)
