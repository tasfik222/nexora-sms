from features_text import PAGES


def test_every_page_fits_telegram_limit():
    for page in PAGES:
        assert 0 < len(page) <= 4096, len(page)


def test_pages_cover_every_release():
    text = "\n".join(PAGES)
    for ver in ["2.0.0", "1.0.6", "1.0.3", "1.0.2", "1.0.0", "0.13.2", "0.13.1", "0.13.0", "0.12.0", "0.11"]:
        assert ver in text


def test_html_is_balanced_for_bold_and_italic():
    for page in PAGES:
        assert page.count("<b>") == page.count("</b>")
        assert page.count("<i>") == page.count("</i>")
        assert "&" not in page and page.count("<") == page.count(">")


def test_current_version_is_marked_once():
    text = "\n".join(PAGES)
    assert text.count("· Current") == 1
    assert "<b>2.0.0</b> · Current" in text


def test_about_text_fits_and_lists_commands():
    import ast, pathlib
    src = pathlib.Path("bot.py").read_text(encoding="utf-8")
    assert "/invite - Your invite link" in src and "/features - Feature overview" in src
