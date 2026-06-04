"""Phase G — extract.dedup 内容指纹(纯逻辑)。"""
from extract.dedup import content_fingerprint


def test_fingerprint_whitespace_invariant():
    assert content_fingerprint("正文 内容\n\n一样", 5, 100) == content_fingerprint("正文内容一样", 5, 100)


def test_fingerprint_distinguishes_content_and_counts():
    base = content_fingerprint("abc", 5, 100)
    assert content_fingerprint("abd", 5, 100) != base
    assert content_fingerprint("abc", 6, 100) != base
    assert content_fingerprint("abc", 5, 200) != base


if __name__ == "__main__":
    test_fingerprint_whitespace_invariant()
    test_fingerprint_distinguishes_content_and_counts()
    print("OK")
