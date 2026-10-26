import json

import web

PAGE = """<html><head><script type="application/ld+json">%s</script></head><body>blah</body></html>"""


def recipe_page(name, rating, count, ings=3, steps=2, shape="plain"):
    node = {"@type": "Recipe", "name": name, "recipeIngredient": [f"{i} cup thing" for i in range(ings)],
            "aggregateRating": {"@type": "AggregateRating", "ratingValue": str(rating), "ratingCount": str(count)}, "totalTime": "PT30M", "recipeYield": ["2 pizzas"]}
    node["recipeInstructions"] = ([{"@type": "HowToStep", "text": f"step {i}"} for i in range(steps)] if shape == "plain"
                                  else [{"@type": "HowToSection", "itemListElement": [{"@type": "HowToStep", "text": f"step {i}"} for i in range(steps)]}])
    return PAGE % json.dumps({"@graph": [{"@type": "WebSite"}, node]})


def test_parses_real_recipe_data_from_a_graph():
    r = web.parse_recipe(recipe_page("Crust", 4.8, 1220), "https://x.test/r")
    assert r["name"] == "Crust" and r["rating"] == 4.8 and r["reviews"] == 1220 and len(r["ingredients"]) == 3 and r["steps"] == ["step 0", "step 1"] and r["yield"] == "2 pizzas"


def test_sectioned_instructions_and_thin_pages():
    assert len(web.parse_recipe(recipe_page("A", 4, 5, steps=3, shape="section"))["steps"]) == 3
    assert web.parse_recipe(recipe_page("B", 4, 5, ings=1)) is None
    assert web.parse_recipe("<html>no data</html>") is None


def test_many_reviews_beat_a_perfect_score_from_a_few():
    few = {"rating": 5.0, "reviews": 3}
    many = {"rating": 4.8, "reviews": 1220}
    assert web.recipe_score(many) > web.recipe_score(few)


def test_find_recipe_picks_the_best_and_never_invents(monkeypatch):
    pages = {"https://a.test": recipe_page("Few", 5.0, 3), "https://b.test": recipe_page("Many", 4.8, 1220), "https://c.test": "<html>nothing</html>"}
    monkeypatch.setattr(web, "web_search", lambda q: [{"title": k, "url": k, "snippet": ""} for k in pages])
    class R:
        def __init__(self, u): self.status, self.body, self.headers, self.encoding = 200, pages[u].encode(), {}, "utf-8"
    monkeypatch.setattr(web, "public_url_problem", lambda u: None)
    monkeypatch.setattr(web, "_scrapling_get", lambda u, **k: R(u))
    out = web.find_recipe("thin crust pizza dough")
    assert out["best"]["name"] == "Many" and out["others"][0]["name"] == "Few" and "source URL" in out["note"]
    monkeypatch.setattr(web, "web_search", lambda q: [{"title": "c", "url": "https://c.test", "snippet": ""}])
    assert "do not invent" in web.find_recipe("x")["error"]
