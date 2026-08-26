from __future__ import annotations

import json
import re

from scripts.widget_pages import embed_page, promo_page


def test_widget_promo_has_canonical_and_structured_data():
    html = promo_page([("Краснодарский край", "krasnodarskiy-kray")],
                      "26 августа, 12:00 МСК")

    assert 'rel="canonical" href="https://tihoenebo.com/widget/"' in html
    document = json.loads(re.search(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    ).group(1))
    assert document["@type"] == "WebPage"


def test_embedded_widget_remains_noindex():
    html = embed_page("Краснодарский край", "krasnodarskiy-kray",
                      "krasnodarskiy_kray", 12)

    assert '<meta name="robots" content="noindex"' in html
