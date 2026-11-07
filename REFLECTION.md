# Reflection

## Development Process & Key Decisions

### Architecture: Text-to-SQL vs RAG
I chose RAG over Text-to-SQL. Census data is a stable annual snapshot — it doesn't change between releases. Pulling it once into a Knowledge Base removes Snowflake as a runtime dependency per query, cutting per-question latency from ~15s (Snowflake round-trip + SQL generation) to ~2–4s (local vector search). The tradeoff is that aggregate queries ("average income across all Midwest states") require the LLM to reason from retrieved text rather than computing via SQL. I note this in the guardrail messaging and handle it by retrieving multiple relevant county documents.

### AgentCore Runtime over Lambda
Lambda has a 15-minute limit, is stateless (all session state must be externalized manually), and streaming requires extra configuration. AgentCore Runtime V2 provides per-session microVM isolation, native 8-hour sessions, built-in streaming, and a snapshot-based cold start — exactly what a chat agent needs. The tradeoff is a slower first deploy (~3 min for V2 snapshot) and higher complexity than a simple Lambda function.

### No API Gateway
AgentCore Runtime supports JWT inbound auth via Cognito OIDC directly. Removing API Gateway eliminates one network hop, reduces cost, and simplifies the infrastructure. The frontend calls the AgentCore endpoint with a Cognito Bearer token — AgentCore validates it without any Lambda proxy.

### AgentCore Memory over DynamoDB
AgentCore Memory replaces what would have been a DynamoDB table of raw JSON message arrays. The four strategies (SUMMARIZATION, USER_PREFERENCE, SEMANTIC, EPISODIC) turn raw conversation events into structured, searchable memories automatically. A returning user gets personalized context without having to replay their entire conversation history.

### Data Granularity: County Level
~3,200 county documents cover the vast majority of natural-language questions ("what's the poverty rate in Cook County?"). Pulling at tract level (~73,000) or block group level (~242,000) would increase KB size and retrieval noise significantly. I note in the agent system prompt that tract-level data is not available, so the agent tells users clearly rather than returning wrong aggregations.

---

## What I'd Improve With More Time

1. **Streaming tokens to the UI**: The current implementation returns the full response as JSON. AgentCore Runtime supports SSE and WebSocket streaming — I'd wire that up for a more responsive chat feel on long answers.

2. **Metadata filtering in KB retrieval**: I'd extract the geography (state/county name) from the user's question and pass it as a metadata filter to the KB query, significantly improving retrieval precision for "what's the income in Travis County?" vs a raw semantic search.

3. **State-level aggregation**: Current documents are county-level. State-level summaries would answer "tell me about California overall" directly rather than retrieving multiple county docs and asking Claude to synthesize.

4. **Multi-year comparison**: The data is ACS 2020. Adding 2016–2019 alongside 2020 would allow temporal questions like "how has poverty changed in Detroit since 2016?"

5. **Comprehensive E2E tests**: Current tests mock all external services. I'd add integration tests against a real Bedrock KB with a small subset of documents, and Playwright tests for the login + chat flow.

---

## Edge Cases Identified but Not Fully Addressed

- **Ambiguous geography**: "What's the income in Springfield?" — there are many Springfields. The agent asks for clarification, but a better implementation would retrieve the top 3 Springfields and present options.
- **Cross-county aggregate queries**: "What's the average income across all rural counties in Ohio?" requires aggregation logic not available in pure RAG. Currently the agent notes this limitation.
- **CBG data missing for some geographies**: Some census block groups have suppressed data (small population). The ETL `safe_agg()` function drops sentinel values (-666666666), so documents for sparse counties may have fewer metrics. The agent doesn't currently surface this to the user.
- **Session after AgentCore microVM termination**: If a session is idle for 15 min, the microVM is terminated. The next message creates a new microVM. Short-term memory reloads from AgentCore Memory correctly, but there's a cold-start delay of a few seconds.

---

## Testing Strategy

**What's covered**:
- Unit tests for all ETL transform functions (safe_agg, cbg_to_county, county_to_document)
- Unit tests for guardrails (pass, block, API error fail-open)
- Unit tests for short-term and long-term memory operations
- Integration tests for the full agent pipeline (all external services mocked)

**Tradeoffs**:
- All AWS service calls are mocked (no real Bedrock/Memory calls in CI). This tests the logic without incurring API cost, but doesn't catch API shape mismatches or permission errors.
- No end-to-end browser tests — would add Playwright targeting the CloudFront URL.

**What I'd add**:
- Contract tests against real Bedrock KB with 5 test documents
- Latency regression tests (assert p95 < 60s for representative queries)
- Adversarial input tests (prompt injection attempts, SQL injection attempts in geography names)
- Test for the memory feedback loop — verify that a stated preference in session 1 appears in session 2's long-term context

---

## AI Tool Usage

This project was built using Kiro (AI coding assistant). I used it for:
- Scaffolding all boilerplate (CDK stacks, Dockerfile, package.json)
- Writing the ETL pandas aggregation logic against the SafeGraph schema
- Generating test cases and mock fixtures
- Looking up current AWS service APIs (AgentCore Runtime V2, AgentCore Memory strategies)

I reviewed and edited all generated code, particularly: the guardrail error handling (fail-open behavior was a deliberate choice), the memory prompt injection format (the XML tags for memory/history), and the ETL column mapping against the actual SafeGraph schema I inspected from the archives.
