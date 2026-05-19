# Quiz Vocabulary Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a CEFR C1 vocabulary cap into the `generate_question` prompt so generated quiz wording (question_text + hint) stays at or below C1, while the target word itself remains exempt.

**Architecture:** Single-edit change to `src/agent/gemini_agent.py::generate_question`. Append a "Vocabulary level" block to the existing f-string prompt between `{type_instruction}` and the "Question type rules:" section. The block interpolates `card.front` to make the exemption concrete. No new files, no new dependencies, no schema changes.

**Tech Stack:** Python 3.13, `google-genai` SDK (already in use), `pytest` with `asyncio_mode = "auto"`.

---

### Task 1: Add C1 vocabulary cap to the quiz prompt

**Files:**
- Modify: `src/agent/gemini_agent.py` (function `generate_question`, lines 126–158)
- Modify: `tests/agent/test_gemini_agent.py` (add new test alongside existing ones)

- [ ] **Step 1: Write the failing test**

Add this test at the end of `tests/agent/test_gemini_agent.py`. It captures the prompt actually sent to `generate_content` and asserts the cap block is present with `card.front` interpolated.

```python
@pytest.mark.asyncio
async def test_generate_question_prompt_includes_c1_vocabulary_cap():
    """The prompt must instruct Gemini to keep question_text and hint at CEFR C1
    or below, while exempting the target word itself. Without this cap, generated
    wording can be harder than the word being tested."""
    agent = GeminiAgent(api_key="test")
    response = _mock_response(
        {
            "question_type": "fill_in_blank",
            "question_text": "She made a solemn ___ to keep her word.",
            "correct_answer": "promise",
            "hint": "- part of speech: noun",
        }
    )
    captured = {}

    async def fake_generate(**kwargs):
        captured["contents"] = kwargs["contents"]
        return response

    with patch.object(
        agent._client.aio.models,
        "generate_content",
        new=AsyncMock(side_effect=fake_generate),
    ):
        await agent.generate_question(
            CardData(card_id=2, front="promise", back="約束", tags=[], deck_name="EN"),
            "common",
            retry_count=0,
            recent_errors=[],
            conversation_summary=None,
        )

    prompt = captured["contents"]
    assert "Vocabulary level" in prompt
    assert "CEFR C1 or below" in prompt
    assert "10000" in prompt
    assert "'promise'" in prompt
    assert "exempt" in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/agent/test_gemini_agent.py::test_generate_question_prompt_includes_c1_vocabulary_cap -v`

Expected: FAIL with `AssertionError` on `assert "Vocabulary level" in prompt` — the current prompt has no such block.

- [ ] **Step 3: Add the vocabulary-cap block to the prompt**

In `src/agent/gemini_agent.py`, locate the f-string prompt inside `generate_question` (around line 126). Currently the prompt looks like:

```python
prompt = (
    "You are a language learning quiz generator.\n"
    f"Card front: {card.front}\nCard back: {card.back}\nTags: {', '.join(card.tags)}\n"
    f"{context}\n"
    f"{type_instruction}\n"
    "Question type rules:\n"
    ...
)
```

Insert a new f-string segment between `f"{type_instruction}\n"` and `"Question type rules:\n"` so the prompt becomes:

```python
prompt = (
    "You are a language learning quiz generator.\n"
    f"Card front: {card.front}\nCard back: {card.back}\nTags: {', '.join(card.tags)}\n"
    f"{context}\n"
    f"{type_instruction}\n"
    "Vocabulary level (applies to ALL of question_text and hint):\n"
    "- Use vocabulary at CEFR C1 or below (roughly the top 10000 most frequent English words).\n"
    f"- The target word itself ('{card.front}') is exempt — it can be any level, since it's what's being tested.\n"
    "- Prefer plainer wording when in doubt. Avoid literary, archaic, or specialised technical vocabulary unless it's the target word.\n"
    "Question type rules:\n"
    ...
)
```

Leave every other line of the prompt unchanged. The em-dash (`—`) is intentional and matches existing prompt style elsewhere in the file.

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/agent/test_gemini_agent.py::test_generate_question_prompt_includes_c1_vocabulary_cap -v`

Expected: PASS.

- [ ] **Step 5: Run the full gemini_agent test file to confirm no regressions**

Run: `uv run pytest tests/agent/test_gemini_agent.py -v`

Expected: all tests in the file PASS (4 pre-existing + 1 new = 5 passed).

- [ ] **Step 6: Run lint, format check, and full test suite**

Run these three in sequence; all must pass before committing:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
```

Expected: zero lint errors, format unchanged, full suite passes.

- [ ] **Step 7: Commit and push**

```bash
git add src/agent/gemini_agent.py tests/agent/test_gemini_agent.py
git commit -m "$(cat <<'EOF'
feat(quiz): cap question and hint wording at CEFR C1

Gemini was free to use arbitrary vocabulary in question_text and hint,
which sometimes made the wording around the target word harder than the
word itself. Adds a Vocabulary level block to the generate_question
prompt: question_text and hint must stay at CEFR C1 or below (≈ top
10000 most frequent English words). The target word (card.front) is
explicitly exempt.

Prompt-only enforcement — no post-generation verification, no retry.
Tracking effectiveness will be manual / by feel; if leaks prove too
frequent, a follow-up can add wordfreq-based validation.
EOF
)"
git push origin main
```

Expected: commit lands on `main`, push succeeds.

---

## Self-review

**Spec coverage** — Every requirement from `docs/superpowers/specs/2026-05-19-quiz-vocab-cap-design.md` is covered:

- Cap at CEFR C1 → Step 3 (literal "CEFR C1 or below" in the prompt)
- ≈10,000 word anchor → Step 3 ("top 10000 most frequent English words")
- Scope = question_text + hint → Step 3 (block header "applies to ALL of question_text and hint")
- Target word exempt with concrete interpolation → Step 3 (`f"... '{card.front}' is exempt ..."`) and Step 1 (test asserts `"'promise'" in prompt`)
- No change to `evaluate_answer`, `classify_register`, `generate_session_summary`, schemas, tests beyond one new case → covered by the single-file edit scope
- Prompt-only enforcement → no verification/retry logic in any task
- Verification commands → Step 6
- Human acceptance check → out of plan, deferred to user after deploy (called out in spec under "Verification")

**Placeholder scan** — No "TBD", "TODO", "implement later", or vague placeholders. Every code block is concrete.

**Type consistency** — Test uses `CardData(card_id=2, front="promise", ...)` matching the existing `_card()` helper's signature on line 12 of the test file. Mock pattern matches existing `AsyncMock` usage. `captured["contents"]` mirrors the keyword arg name used in `_structured` (`contents=prompt`, line 46 of gemini_agent.py).

---

## Execution choice

Plan complete and saved to `docs/superpowers/plans/2026-05-19-quiz-vocab-cap.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Overkill for a 7-step single-task plan, but it's the recommended flow.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Probably the right call here given size.

Which approach?
