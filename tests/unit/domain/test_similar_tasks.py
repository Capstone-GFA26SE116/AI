from metanoia_ai.domain.similar_tasks import build_search_query, search_terms

# --- T1: the text to embed ---


def test_t1_search_query_is_masked_and_prefixed():
    assert (
        build_search_query("Landing page cho ACME", "Coding", "ACME")
        == "task: search result | query: Landing page cho [client]. Coding"
    )


def test_search_query_without_client():
    assert (
        build_search_query("Viết báo cáo tuần", "Writing", None)
        == "task: search result | query: Viết báo cáo tuần. Writing"
    )


def test_search_query_masks_email_and_phone():
    query = build_search_query("Gửi a.b@acme.com, gọi 0901 234 567", "Sales", None)
    assert query == "task: search result | query: Gửi [email], gọi [phone]. Sales"


# --- T2: full-text terms ---


def test_t2_terms_keep_only_meaningful_words():
    terms = search_terms(
        "Landing page (v2) cho ACME, gửi a.b@acme.com - 0901 234 567 & a!",
        "Coding",
        "ACME",
    )
    assert terms == "landing | page | v2 | cho | gửi | coding"


def test_t2_vietnamese_letters_are_kept():
    assert search_terms("Sửa lỗi đăng nhập", "Coding", None) == (
        "sửa | lỗi | đăng | nhập | coding"
    )


def test_terms_are_not_repeated():
    assert search_terms("Coding coding CODING", "Coding", None) == "coding"


def test_terms_have_no_tsquery_operators():
    terms = search_terms("a & b | c ! d:* (e) 'f' <-> g_h", "Ops", None)
    assert terms == "ops"  # every 1-letter word dropped; "_" splits g_h
    assert not set("&!():*'<>") & set(terms)


# --- T3: nothing left ---


def test_t3_only_symbols_gives_no_terms():
    assert search_terms("!!! --- ???", "", None) is None


def test_t3_only_masks_gives_no_terms():
    assert search_terms("ACME a.b@acme.com", "", "ACME") is None
