import pytest

from crespoqa.templates_store.seed import seed_templates
from crespoqa.templates_store.textfmt import TextError, from_text, to_text


def dump(steps):
    return [s.model_dump(mode="json") for s in steps]


def test_seed_templates_round_trip():
    for t in seed_templates():
        assert dump(from_text(to_text(t.steps))) == dump(t.steps)


def test_all_features_parse():
    steps = from_text('''
# a note
period: fill "Period:" "Date:"
placeholders: "[A \\"q\\"]" = consultant ; "{{C}}" = date
replace: "x" -> "y" @page1 ; "a;b" -> ""
off toc
details: title "Doc" "Details" ; page none ; set author = consultant ; set client = client ; row "Author:" = reviewer
tables: cantsplit off ; header off ; empty-page-breaks keep
refresh
''')
    d = dump(steps)
    assert d[0]["params"] == {"labels": ["Period:", "Date:"], "period_label_action": "fill"}
    assert d[1]["params"]["mappings"][0] == {"token": '[A "q"]', "field": "consultant"}
    assert d[1]["params"]["mappings"][1]["field"] == "report_date"
    assert d[2]["params"]["rules"] == [{"find": "x", "replace": "y", "scope": "page1"}, {"find": "a;b", "replace": "", "scope": "all"}]
    assert d[3]["enabled"] is False
    assert d[4]["params"]["fallback_page"] is None and d[4]["params"]["property_mappings"] == {"author": "consultant", "client": "client"}
    assert d[5]["params"]["cant_split"] is False and d[5]["params"]["header_repeat"] == "off"
    assert dump(from_text(to_text(steps))) == d


@pytest.mark.parametrize("text, needle", [
    ("periodd: remove", "Line 1: unknown step 'periodd'"),
    ("toc\nperiod: delete", "Line 2"),
    ('placeholders: "x" = boss', "not a form field"),
    ('replace: "x" > "y"', "write it like"),
    ("tables: cantsplit maybe", "could not read"),
    ("off", "must be followed"),
    ("# only a comment", "no steps"),
])
def test_errors_name_the_line(text, needle):
    with pytest.raises(TextError, match=needle):
        from_text(text)


def test_text_save_creates_version_via_api(settings):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from crespoqa.api.app import create_app
    from crespoqa.api.auth import hash_password
    app = create_app(replace(settings))
    app.state.store.add_user("ed", hash_password("editor-pass-1"), "editor")
    with TestClient(app, headers={"X-Requested-With": "CrespoQA"}) as c:
        c.post("/api/login", json={"username": "ed", "password": "editor-pass-1"})
        t = c.get("/api/templates").json()["templates"][0]
        got = c.get(f"/api/templates/{t['id']}").json()
        assert got["text"].startswith("# CrespoQA template")
        r = c.put(f"/api/templates/{t['id']}", json={"name": t["name"], "text": got["text"].replace("period: remove", "period: fill")})
        assert r.json() == {"version": 2}
        assert c.get(f"/api/templates/{t['id']}").json()["steps"][0]["params"]["period_label_action"] == "fill"
        bad = c.put(f"/api/templates/{t['id']}", json={"name": "x", "text": "toc\nbogus: 1"})
        assert bad.status_code == 400 and "Line 2" in bad.json()["detail"]
        pv = c.post("/api/templates/preview", json={"text": "toc\nrefresh"}).json()["description"]
        assert len(pv) == 2
