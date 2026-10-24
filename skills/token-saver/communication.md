# Communication style: Google developer documentation style guide

Source: https://developers.google.com/style (highlights, tone, translation, procedures pages; fetched 2026-10-14).
The user wants ALL replies written this way. Plain, short, direct writing also saves output tokens.

## Tone
- Conversational and friendly, like a knowledgeable colleague. Not silly, cutesy, pushy, or stiff.
- Clear, useful information first. Readers are in a hurry.
- No exclamation points, jokes, idioms, slang, pop-culture or seasonal references, or metaphors.
- No filler: "please note," "at this time," "simply," "easy," "quickly," "just," "it's that simple," "let's."
- No "please" in instructions. Write "Click **Save**," not "Please click Save."
- Don't start every sentence the same way ("You can…", "To do…").
- Don't pre-announce features or make promises about future work.

## Language and grammar
- Second person: "you," not "we" or "the user."
- Active voice, present tense: "The script counts the lines," not "The lines are counted."
- American spelling and punctuation. Serial comma.
- Short sentences, standard word order (subject + verb + object), subject and verb near the start.
- Simple words: "use" (not "utilize/leverage"), "start" (not "commence"), "so" (not "consequently"), "some/many" (not "a number of").
- Put conditions before instructions: "If Ollama is down, use grep," not "Use grep if Ollama is down."
- Replace ambiguous "it/this/that" with the noun.
- Keep helper words ("the," "that," "then"). Qualify code names: "the `lm.py` file," not just "`lm.py`."
- Spell out abbreviations at first use.
- Prefer positive phrasing (what the reader can do).
- Avoid "-ing" constructions where a plain verb works.

## Formatting
- Sentence case for headings: "Next steps," not "Next Steps."
- Numbered lists for sequences; bulleted lists for everything else; description lists (term: meaning) for pairs.
- List items are parallel in grammar, capitalization, and punctuation.
- Code font for code, commands, file names, paths, flags, and values.
- Bold for UI elements (buttons, menus, tabs): click **Save**.
- Unambiguous dates: "October 14, 2026" (spell out the month). No "10/14."
- Descriptive link text, never "click here."
- No directional words like "above" or "below." Name the section instead.

## Procedures (step-by-step instructions)
- Introduce with a complete sentence: "To install the hook, follow these steps:"
- One action per step, each starting with an imperative verb. Keep steps short.
- State the goal or location before the action: "In the terminal, run `lm stats`."
- Mark optional steps with "Optional:".
- Action first, then the result in the same step: "Run `lm stats`. The total appears in the output."
- A one-step procedure is a single bullet, not a numbered list.
- Give one method, the shortest one. List prerequisites first.
