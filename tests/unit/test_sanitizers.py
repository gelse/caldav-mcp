"""Unit tests for caldav_mcp.sanitizers — input sanitisation and validation."""

from caldav_mcp.sanitizers import (
    MAX_CALENDAR_NAME_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_LOCATION_LENGTH,
    MAX_QUERY_LENGTH,
    MAX_SUMMARY_LENGTH,
    limit_string_length,
    sanitize_text,
    validate_calendar_name,
    validate_email,
)


# ---------------------------------------------------------------------------
# sanitize_text
# ---------------------------------------------------------------------------

def test_sanitize_text_strips_control_characters():
    """Control chars (except \\t \\n \\r) are removed."""
    dirty = "Hello\x00\x01\x02World"
    assert sanitize_text(dirty) == "HelloWorld"


def test_sanitize_text_allows_tab_newline_cr():
    """Tabs, newlines, and carriage returns are preserved."""
    value = "line1\nline2\tline3\rline4"
    assert sanitize_text(value) == value


def test_sanitize_text_strips_leading_trailing_whitespace():
    assert sanitize_text("  hello  ") == "hello"


def test_sanitize_text_raises_on_too_long():
    long = "x" * (MAX_DESCRIPTION_LENGTH + 1)
    import pytest
    with pytest.raises(ValueError, match="maximum length"):
        sanitize_text(long)


def test_sanitize_text_accepts_custom_max_length():
    import pytest
    with pytest.raises(ValueError, match="maximum length"):
        sanitize_text("abcdef", max_length=3)


def test_sanitize_text_empty_string():
    assert sanitize_text("") == ""


def test_sanitize_text_allows_unicode_letters():
    assert sanitize_text("Ünïcödé Tëxt 🎉") == "Ünïcödé Tëxt 🎉"


# ---------------------------------------------------------------------------
# validate_calendar_name
# ---------------------------------------------------------------------------

def test_validate_calendar_name_accepts_valid():
    assert validate_calendar_name("Personal") == "Personal"


def test_validate_calendar_name_accepts_hyphens_underscores():
    assert validate_calendar_name("work-team_2024") == "work-team_2024"


def test_validate_calendar_name_accepts_dots_slashes():
    assert validate_calendar_name("archive/2024.01") == "archive/2024.01"


def test_validate_calendar_name_strips_whitespace():
    assert validate_calendar_name("  Personal  ") == "Personal"


def test_validate_calendar_name_rejects_empty():
    import pytest
    with pytest.raises(ValueError, match="must not be empty"):
        validate_calendar_name("")


def test_validate_calendar_name_rejects_only_whitespace():
    import pytest
    with pytest.raises(ValueError, match="must not be empty"):
        validate_calendar_name("   ")


def test_validate_calendar_name_rejects_special_characters():
    import pytest
    with pytest.raises(ValueError, match="invalid characters"):
        validate_calendar_name("test<script>")


def test_validate_calendar_name_rejects_too_long():
    import pytest
    name = "a" * (MAX_CALENDAR_NAME_LENGTH + 1)
    with pytest.raises(ValueError, match="exceeds"):
        validate_calendar_name(name)


def test_validate_calendar_name_accepts_max_length():
    name = "a" * MAX_CALENDAR_NAME_LENGTH
    assert validate_calendar_name(name) == name


def test_validate_calendar_name_allows_unicode():
    assert validate_calendar_name("Familie Müller") == "Familie Müller"


# ---------------------------------------------------------------------------
# validate_email
# ---------------------------------------------------------------------------

def test_validate_email_accepts_valid():
    assert validate_email("user@example.com") == "user@example.com"


def test_validate_email_strips_whitespace():
    assert validate_email("  user@example.com  ") == "user@example.com"


def test_validate_email_accepts_subdomains():
    assert validate_email("a@b.c.d.example.com") == "a@b.c.d.example.com"


def test_validate_email_accepts_plus_addressing():
    assert validate_email("user+tag@example.com") == "user+tag@example.com"


def test_validate_email_rejects_empty():
    import pytest
    with pytest.raises(ValueError, match="must not be empty"):
        validate_email("")


def test_validate_email_rejects_no_at():
    import pytest
    with pytest.raises(ValueError, match="Invalid email"):
        validate_email("userexample.com")


def test_validate_email_rejects_no_domain():
    import pytest
    with pytest.raises(ValueError, match="Invalid email"):
        validate_email("user@")


def test_validate_email_rejects_no_tld():
    import pytest
    with pytest.raises(ValueError, match="Invalid email"):
        validate_email("user@example")


def test_validate_email_rejects_spaces():
    import pytest
    with pytest.raises(ValueError, match="Invalid email"):
        validate_email("user name@example.com")


# ---------------------------------------------------------------------------
# limit_string_length
# ---------------------------------------------------------------------------

def test_limit_string_length_within_limit():
    assert limit_string_length("hello", 10) == "hello"


def test_limit_string_length_at_limit():
    value = "x" * 10
    assert limit_string_length(value, 10) == value


def test_limit_string_length_exceeds_limit():
    import pytest
    with pytest.raises(ValueError, match="exceeds maximum length"):
        limit_string_length("x" * 11, 10)


def test_limit_string_length_custom_field_name():
    import pytest
    with pytest.raises(ValueError, match="summary"):
        limit_string_length("x" * 11, 10, field_name="summary")


def test_limit_string_length_empty_string():
    assert limit_string_length("", 10) == ""
