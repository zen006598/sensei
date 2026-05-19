# Feedback Must Not Leak Target Word — Design

## Problem

`GeminiAgent.evaluate_answer` returns a `suggestion` string that is shown to the learner immediately after a wrong answer. For `spelling` and `fill_in_blank` questions, the target word is hidden from the learner — they have to produce it. The state machine retries the card with another question after a wrong answer, so the learner is supposed to keep working at it.

Observed in production: when the learner answered `cartoon` to a question whose target was `crate`, the bot returned:

> Incorrect
> Different word. A 'crate' is a sturdy wooden box used for moving items, while a 'cartoon' refers to an animated film or drawing.

The target word `crate` appears verbatim in the `suggestion`. Combined with the next-round hint (which legitimately includes letter structure like "5 letters · starts with 'C' · ends with 'E'"), the learner now has the answer handed to them and the retry loop is broken.

Root cause is in the prompt for `evaluate_answer` (`src/agent/gemini_agent.py:211–213`):

```
wrong (spelling/fill_in_blank) → start with a closeness indicator:
'Very close — N letter(s) off' for typos ≤2 chars, or 'Different word' when the answer is unrelated.
Then optionally a brief hint about the part that's wrong (e.g. 'check the vowel in the middle').
```

"Optionally a brief hint" is too permissive. Gemini interpreted it as "explain what the correct word means", which contains the word.

## Goal

For `wrong` outcomes on `spelling` and `fill_in_blank` questions, the `suggestion` must:

- give a closeness indicator (kept as today),
- optionally provide one short *directional* nudge — a thematic category or context — without naming, defining, or describing the spelling of the target word.

## Non-goals

- No change to `correct` outcomes (already either empty or a "Native tip" for sentence questions).
- No change to `wrong` / `grammar_error` for `sentence` questions — the target word is already stated in the question, so naming it in the suggestion is fine and useful (the correction format already calls for it).
- No change to `generate_question`, schemas, dataclasses, state machine routing, or the existing `MUST NOT contain the target word` rule in `generate_question`'s hints.
- No programmatic post-generation redaction in this change. Prompt-only enforcement, like the C1 vocab cap. If leaks persist, a follow-up can add a deterministic substring redact pass.

## Design

Replace lines 211–213 of `src/agent/gemini_agent.py` (the `wrong (spelling/fill_in_blank)` clause inside `evaluate_answer`'s prompt) with:

```
wrong (spelling/fill_in_blank) → start with a closeness indicator:
'Very close — N letter(s) off' for typos ≤2 chars, or 'Different word' when the answer is unrelated.
Then optionally ONE short directional nudge — only a thematic category or context
(e.g. 'think about transport', 'related to weather', 'a feeling, not an object').
The suggestion MUST NOT:
- contain the target word ('{correct_answer}') or any inflected/stemmed form of it,
- define, paraphrase, or explain what the target word means,
- reveal the target word's length, first/last letter, or any letter-by-letter structure,
- use a synonym so close it gives the answer away (e.g. 'box' for 'crate').
```

The `{correct_answer}` placeholder is interpolated into the f-string `prompt = (...)` block. `correct_answer` is already in scope (it is a parameter of `evaluate_answer`).

The rest of the `evaluate_answer` prompt — scoring guide, acceptable examples, the `correct (sentence)` Native tip rule, the `correct (spelling/fill_in_blank)` leave-empty rule, the `wrong/grammar_error (sentence)` correction-bullet rule — stays exactly as today.

## Testing

Add one test to `tests/agent/test_gemini_agent.py`, modelled on `test_generate_question_prompt_includes_c1_vocabulary_cap`:

- Call `evaluate_answer` with `question_type="fill_in_blank"`, `correct_answer="crate"`, `user_answer="cartoon"`.
- Capture the prompt sent to `generate_content` via `AsyncMock(side_effect=...)`.
- Assert the prompt contains the new constraint markers: `"MUST NOT"`, `"'crate'"` (interpolated), and at least one of the new clause keywords (e.g. `"directional nudge"` or `"thematic category"`).

The existing `test_evaluate_answer_returns_judge_result` test continues to pass unchanged (it asserts parsing behaviour, not prompt content).

## Verification

- Mechanical: `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run pytest -q` — all must pass.
- Human: after deploy, deliberately answer a `spelling` or `fill_in_blank` question wrong with an unrelated word; confirm the bot's reply does not contain the target word and does not define it.

## Risks and known limitations

- **Prompt-only enforcement.** Like the C1 vocab cap, this is a soft constraint on a Flash-tier model. Expect ~80–90% compliance. If leaks persist in practice, follow-up: add a post-generation pass in `evaluate_answer` that does a deterministic case-insensitive substring check for `correct_answer` (and a small inflection set) in the returned `suggestion`, and replaces hits with `[hidden]`. That is explicitly out of scope here.
- **"One short directional nudge" is judgemental.** A category like "think about transport containers" is arguably as helpful as just saying `crate`. The MUST-NOT list and the explicit `'box' for 'crate'` example are there to push back; the rest is on Gemini's judgement.
- **`correct_answer` shape assumption.** For `spelling` and `fill_in_blank`, `correct_answer` is a single word. For `sentence` questions it can be a full sentence — but this clause only applies to spelling/fill_in_blank, so the interpolation is safe. (We still pass `correct_answer` through unchanged; the clause label scopes it.)

## Out of scope (explicit non-decisions)

- Reworking the broader feedback UX (e.g. a "give up" / "show answer" command, dedicated reveal screen).
- Adding programmatic redaction of the target word from the returned suggestion.
- Tightening hint generation in `generate_question` further — the existing "MUST NOT contain the target word" rule already prevents leaks there, and the user's report confirms the hint side was fine; only the suggestion side leaked.
