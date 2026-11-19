# ADR 0004: Grounded Gemini AI Pipeline & Fixed Read-Only Tools
- **Status**: Accepted
- **Context**: Unconstrained LLMs can hallucinate prices or contradict deterministic risk rejections.
- **Decision**: Build immutable `EvidencePacket` (`F01..Fnn`), validate all numbers and `fact_id` references via `validate_llm_explanation`, retry once on failure, and fall back to `DETERMINISTIC_TEMPLATE`. Expose only 4 read-only tools (`get_analysis`, `get_plan`, `calc_quantity`, `get_journal_stats`).
