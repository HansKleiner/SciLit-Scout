from scilit_scout.core import norm_doi, fingerprint, parse_year, suggest_category, normalize

def test_doi():
    assert norm_doi("https://doi.org/10.1234/ABC.") == "10.1234/abc"

def test_year():
    assert parse_year(None, "2022-04-13") == 2022
    assert parse_year("2021") == 2021
    assert parse_year("not dated") is None

def test_category():
    assert suggest_category("Piezoelectric pavement") == "T1"

def test_fingerprint_doi():
    a = {"doi": "10.1234/ABC", "document_type": "publication", "title": "A"}
    b = {"doi": "https://doi.org/10.1234/abc", "document_type": "publication", "title": "B"}
    assert fingerprint(a) == fingerprint(b)

def test_normalize_year():
    row = normalize({"title": "Test", "year": "2020", "doi": ""}, "test")
    assert row["year"] == 2020


def test_openaire_record_mapping():
    from scilit_scout.core import _openaire_record

    item = {
        "id": "openaire-id-1",
        "mainTitle": "Российская научная статья",
        "publicationDate": "2022-05-10",
        "authors": [{"fullName": "Иван Иванов"}, {"fullName": "Пётр Петров"}],
        "pids": [{"scheme": "doi", "value": "10.1234/ABC"}],
        "descriptions": ["Аннотация статьи"],
        "language": {"code": "rus", "label": "Russian"},
        "subjects": [{"subject": {"value": "энергетика"}}],
        "instances": [{"urls": ["https://example.org/article"]}],
    }
    row = _openaire_record(item)
    assert row["title"] == "Российская научная статья"
    assert row["authors"] == "Иван Иванов; Пётр Петров"
    assert row["year"] == 2022
    assert row["doi"] == "10.1234/ABC"
    assert row["abstract"] == "Аннотация статьи"
    assert row["url"] == "https://example.org/article"
    assert row["language"] == "Russian"
    assert row["openaire_id"] == "openaire-id-1"
