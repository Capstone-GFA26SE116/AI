import pytest

from metanoia_ai.domain.memory_text import mask_contact

# --- T3: emails and phone numbers ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail an.nguyen+work@acme.com.vn now", "mail [email] now"),
        ("call 0901 234 567 today", "call [phone] today"),
        ("call +84 90-123-4567 today", "call [phone] today"),
        ("call 0901.234.567", "call [phone]"),
        ("call 0901234567", "call [phone]"),
    ],
)
def test_t3_emails_and_phones_are_masked(text, expected):
    assert mask_contact(text, client_name=None) == expected


def test_t3_title_and_note_together():
    text = "Gửi file cho a@b.co, gọi 0901 234 567 hoặc +84 90-123-4567"
    assert mask_contact(text, None) == "Gửi file cho [email], gọi [phone] hoặc [phone]"


# --- T4: client name, any case ---


@pytest.mark.parametrize(
    "text",
    ["Landing page cho Acme Corp", "landing page cho ACME CORP", "acme corp: sửa logo"],
)
def test_t4_client_name_is_masked_ignoring_case(text):
    assert "[client]" in mask_contact(text, client_name="Acme Corp")
    assert "acme" not in mask_contact(text, client_name="Acme Corp").lower()


def test_t4_vietnamese_client_name():
    result = mask_contact("Làm logo cho Công ty Hoa Mai", client_name="công ty hoa mai")
    assert result == "Làm logo cho [client]"


def test_client_name_is_matched_as_a_whole_word():
    # Client "An" must not break words that only contain the letters "an".
    assert (
        mask_contact("Analytics cho An", client_name="An") == "Analytics cho [client]"
    )


def test_email_with_client_name_is_masked_as_one_email():
    assert mask_contact("gửi bob@acme.com", client_name="Acme") == "gửi [email]"


def test_client_name_with_special_characters():
    assert (
        mask_contact("Việc cho A.B (VN)", client_name="A.B (VN)") == "Việc cho [client]"
    )


# --- T5: no client (Personal project) ---


@pytest.mark.parametrize("client_name", [None, "", "   "])
def test_t5_no_client_changes_nothing(client_name):
    text = "Viết blog cá nhân"
    assert mask_contact(text, client_name) == text


# --- T7: dates and short numbers are not phone numbers ---


@pytest.mark.parametrize(
    "text",
    [
        "Hạn 14-10-2026",
        "Hạn 14/10/2026",
        "Sửa 3 lần",
        "Version 2.1.4",
        "Năm 2026, mã 12345678",
    ],
)
def test_t7_dates_and_short_numbers_stay(text):
    assert mask_contact(text, None) == text
