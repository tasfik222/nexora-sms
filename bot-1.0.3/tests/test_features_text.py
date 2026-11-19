from features_text import PAGES


def test_every_page_fits_telegram_limit():
    for page in PAGES:
        assert 0 < len(page) <= 4096, len(page)


def test_pages_cover_every_release():
    text = "\n".join(PAGES)
    for ver in ["1.0.2", "1.0.0", "0.13.2", "0.13.1", "0.13.0", "0.12.0", "0.11"]:
        assert ver in text


def test_html_is_balanced_for_bold_and_italic():
    for page in PAGES:
        assert page.count("<b>") == page.count("</b>")
        assert page.count("<i>") == page.count("</i>")
        assert "&" not in page and page.count("<") == page.count(">")
