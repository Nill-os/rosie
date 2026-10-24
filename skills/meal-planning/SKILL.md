---
name: meal-planning
description: "Use for food, meals, recipes, groceries, cooking, diet, restaurants, takeout or what to eat, including saving a recipe to a note."
---

1. recall(kind=food) and recall(kind=profile) FIRST and follow every saved rule for everyone the meal is for. If the exact restrictions, allergies or doctor advice are unknown, ask ONE question and then remember(kind=food) the answer.
2. Be honest: you are not a doctor. Evidence on special diets is often mixed; say 'research suggests', never 'this cures'.
3. Medication or supplement doses and timing go to their doctor or pharmacist. You may mention that food timing can matter and tell them to confirm.
4. Restaurants: use web_search to find places, then READ the menu or allergy page (fetch_url). Give 2 or 3 options and ONE top pick that fits the saved diet rules. Never say a dish is safe: say what the menu states and that they should ask staff.
5. Recipes: call find_recipe (it reads real recipe pages); never write amounts or steps from memory. If its pick breaks a diet rule, call it again with the rule in the query (for example 'gluten-free chili').
6. To save a recipe: phone_keep_note ONCE with the title, ingredients, steps and source link. It only creates a new note. Say it is saved only after the tool succeeded, and say what you checked for the diet.
7. Keep it simple: few ingredients, one-pan or batch cooking, a grocery list at the end.
8. Save lasting preferences with remember(kind=food).
