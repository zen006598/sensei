# Quiz Vocabulary Cap — Design

## Problem

`GeminiAgent.generate_question` currently places no constraint on the difficulty of vocabulary used in generated question text or hints. As a result, Gemini occasionally produces fill-in-blank context sentences, sentence-task scenarios, spelling definitions, and hint bullets that are *harder* than the target word being tested. For a learner aiming at the C1 level, this is counter-productive — the wording around the test word should not itself be a comprehension obstacle.

## Goal

Cap the vocabulary used in generated questions to **CEFR C1 or below** (≈ top 10,000 most frequent English words). The cap applies to the wording **around** the test word, not to the test word itself.

## Non-goals

- No change to `evaluate_answer`. The native-tip / grammar-correction suggestions stay as-is.
- No change to `generate_session_summary`.
- No automatic verification of LLM output. No retry on violation. Prompt-only enforcement.
- No new vocabulary-list file, no new runtime dependency. The existing `wordfreq` dep is unrelated to this work.

## Scope of the cap

Applies to:

- `question_text` for all three question types (`fill_in_blank` context sentence, `sentence` task scenarios, `spelling` definition).
- `hint` field — all bullets, including synonyms, collocations, example sentences, meaning explanations.

Exempt:

- The target word itself (`card.front`). It is the thing being tested and can be at any level.
- Any inflected/stemmed form of the target word inside the hint is already disallowed by the existing "MUST NOT contain the target word" rule — that constraint stays.

## Design

Single change: insert a "Vocabulary level" block into the `generate_question` prompt in `src/agent/gemini_agent.py`. Placement is immediately after `{type_instruction}` and before the "Question type rules:" section, so the cap is part of active context by the time the model reads the per-type rules.

Block content (verbatim):

```
Vocabulary level (applies to ALL of question_text and hint):
- Use vocabulary at CEFR C1 or below (roughly the top 10000 most frequent English words).
- The target word itself ('{card.front}') is exempt — it can be any level, since it's what's being tested.
- Prefer plainer wording when in doubt. Avoid literary, archaic, or specialised technical vocabulary unless it's the target word.
```

The block is f-string interpolated with `card.front` so the exemption is concrete to the model.

## What does not change

- `_structured` / `QUIZ_SCHEMA` — no schema fields added.
- Existing prompt sections (question type rules, hint format rules, the "CRITICAL: hint MUST NOT contain the target word" rule). They stay in the same order; the new block is inserted between two existing ones.
- `evaluate_answer`, `classify_register`, `generate_session_summary`.
- Tests. Existing unit tests mock the Gemini call and assert on parsing/dispatch logic, not prompt content — they should pass unchanged.

## Verification

- Mechanical: lint (`uv run ruff check`), format (`uv run ruff format --check`), tests (`uv run pytest -q`) must all still pass.
- Human acceptance: after deploy, run a few `/quiz` rounds in production and subjectively check that question wording reads at C1-or-below level. No metric, no dashboard — this is a feel check.

## Risks and known limitations

- **No verification loop.** Prompt-only enforcement on a Flash-tier model is typically ~80–90% compliant. Occasional C2 vocabulary leaks are expected. If the leak rate proves too high in practice, a follow-up could add post-generation verification using `wordfreq.zipf_frequency` as a proxy — but that is explicitly out of scope here.
- **CEFR ↔ frequency mismatch.** "Top 10,000 most frequent" is given as an anchor for the model; real CEFR levels are not strictly frequency-determined. Acceptable trade-off for a one-line prompt change.
- **Target word exemption phrased by interpolation.** If `card.front` contains an HTML sound tag or other Anki cruft (already seen in production logs, e.g. `'flamboyant [sound:...mp3]'`), the exemption sentence will still be readable to the model — it just contains the noisy form. This does not affect compliance because the existing prompt already passes the same noisy `card.front` elsewhere.

## Out of scope (explicit non-decisions)

- Whether to lower the cap to B2 / raise to C2 / make it user-configurable. Cap is fixed at C1 in this change.
- Whether to apply the cap to `evaluate_answer` output ("Native tip" rephrasings). User explicitly chose to leave evaluation untouched.
- Whether to ship a CEFR-tagged wordlist for programmatic checks. Not needed; prompt-only.
