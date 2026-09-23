from policy_advisor.ingestion.language_detect import DEFAULT_LANGUAGE, detect_language


def test_detects_english():
    text = "The defendant shall file a defence within five days of service of the summons upon him."
    assert detect_language(text) == "en"


def test_detects_french():
    text = "Le défendeur doit déposer une défense dans un délai de cinq jours à compter de la signification."
    assert detect_language(text) == "fr"


def test_falls_back_to_default_for_short_text():
    assert detect_language("ok") == DEFAULT_LANGUAGE


def test_falls_back_to_default_for_unsupported_language():
    # German - long enough to classify confidently, but outside the two
    # languages this phase has been validated against.
    text = "Der Beklagte muss innerhalb von fünf Tagen nach Zustellung der Vorladung eine Verteidigung einreichen."
    assert detect_language(text) == DEFAULT_LANGUAGE
