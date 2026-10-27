from unittest.mock import Mock

from agent.ingest_iasa import ingest_folder


def test_ingest_folder_uploads_each_source_and_indexes_supported_documents(tmp_path):
    source = tmp_path / "IASA"
    nested = source / "policies"
    nested.mkdir(parents=True)
    (nested / "policy.txt").write_text("Policy text", encoding="utf-8")
    (source / "unknown.bin").write_bytes(b"opaque")
    s3 = Mock()
    vectors = Mock()
    embed = Mock(return_value=[0.25] * 1024)

    report = ingest_folder(
        source,
        source_bucket="project1-dev-iasa-kb",
        vector_bucket="project1-dev-iasa-vectors",
        index_name="iasa-knowledge",
        s3_client=s3,
        vectors_client=vectors,
        embed=embed,
    )

    assert report == {"uploaded": 2, "indexed": 1, "failed": 1}
    assert {call.kwargs["Key"] for call in s3.put_object.call_args_list} == {
        "source/iasa/policies/policy.txt",
        "source/iasa/unknown.bin",
    }
    vectors.put_vectors.assert_called_once()
