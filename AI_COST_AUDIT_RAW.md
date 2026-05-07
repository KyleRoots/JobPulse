# Scout Genius — AI Cost Optimization Audit (Raw)

Read-only characterization of every OpenAI call site listed in the audit
brief. One section per call site. Recommendations follow the strict
T1/T2/T3 guide; rationales explain why.

---

### [1] scout_prospector_service.py:233 — prospect_company_research_pass
- **Current model**: gpt-5.4 (Responses API with `web_search_preview` tool)
- **Function/method**: `_execute_single_pass` — one of N angled web-search research passes used to discover prospect companies for a recruiter ICP profile.
- **Prompt complexity**: Open-ended research instructions ("find companies matching this ICP using web search, return JSON with name/score/justification"). System+user prompts are medium (500–2000 tokens) but the model invokes the web_search tool, so true cost is dominated by tool-call expansion and the 16k output budget. Effective input tokens: large (2000–8000) once tool results are stitched in.
- **Output structure**: JSON list of company objects (name, score, justification) plus a free-text summary.
- **Recruiter visibility**: HIGH — output is shown directly in the prospector dashboard and drives outbound research.
- **Reasoning tier**: T3 — open-ended discovery + ranking with web search.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Web-search research with quality ranking is exactly what the flagship model exists for. mini cannot reason over fresh search results to score and justify a prospect list. Downgrading risks a visible quality regression in a recruiter-facing surface.

---

### [2] scout_prospector_service.py:519 — icp_refine_criteria
- **Current model**: gpt-5.4 (Responses API, no tools)
- **Function/method**: `refine_criteria` — converts a recruiter's plain-English ICP description into structured suggested industries / sizes / geographies / job_types / signals.
- **Prompt complexity**: Small (<500 tokens). Single short paragraph + JSON schema instructions, capped 1024 output tokens.
- **Output structure**: JSON object with five suggestion arrays + brief refinement_notes.
- **Recruiter visibility**: MEDIUM — surfaces as suggested fields in a form the recruiter then edits before saving.
- **Reasoning tier**: T2 — bounded inference (taxonomy-style suggestions) with light interpretation of free text.
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: Mini can almost certainly produce the same suggested industries/sizes/geographies, but the recruiter sees the output and edits from it, so a quality regression would be noticed. Worth A/B testing on ~50 ICPs side-by-side before flipping.

---

### [3] job_classification_service.py:121 — job_taxonomy_classifier
- **Current model**: gpt-5.4
- **Function/method**: `classify_job` — assigns a job to LinkedIn's closed taxonomy (job_function / industry / seniority_level).
- **Prompt complexity**: Medium (500–2000 tokens). Job title + description + the three closed lists. response_format=json_object, 1500 output tokens.
- **Output structure**: JSON object with three picklist values from fixed lists.
- **Recruiter visibility**: LOW — used as metadata for filtering/analytics.
- **Reasoning tier**: T1 — pure closed-taxonomy classification. The system prompt explicitly forbids new categories.
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: Closed-taxonomy classification is the textbook mini use case. There is no reasoning beyond "pick the closest of N labels". Downgrading saves ~90% with no quality risk; can be reverted instantly if anomalies appear in the metadata distribution.

---

### [4] scout_vetting_service.py:326 — vetting_question_generation
- **Current model**: gpt-5.4
- **Function/method**: `_generate_vetting_questions` — writes 3–5 friendly verification questions for a candidate based on the screening match summary, skills assessment, experience assessment, and identified gaps.
- **Prompt complexity**: Medium (500–2000 tokens). Includes prior screening summary + skills + gaps + tone/style rules.
- **Output structure**: JSON array of question strings (capped at 5).
- **Recruiter visibility**: HIGH — questions are sent directly to the candidate via email under the "Scout by Myticas" persona.
- **Reasoning tier**: T2 — bounded creative writing (must reference real gaps, follow tone rules, avoid open-ended questions). Quality matters because candidates judge the brand by tone.
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: Mini can write competent recruiter-style questions, but tone and specificity ("reference the actual technology") matter for candidate experience. A/B test on ~30 sessions and have a recruiter blind-rate before flipping.

---

### [5] scout_vetting_service.py:589 — vetting_reply_classifier
- **Current model**: gpt-5.4
- **Function/method**: `_classify_candidate_reply` — classifies a candidate's emailed reply into intent (answer/question/decline/out_of_office/spam/unrelated) and extracts which of our questions were answered.
- **Prompt complexity**: Medium (500–2000 tokens). Conversation context + rules + closed intent taxonomy.
- **Output structure**: JSON object: intent label + reasoning + answers_extracted dict + candidate_questions array.
- **Recruiter visibility**: MEDIUM — drives the next-action branch (continue / decline / unresponsive); answers_extracted is stored on the session for the recruiter handoff.
- **Reasoning tier**: T2 — closed-taxonomy intent classification PLUS answer-to-question matching with paraphrasing.
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: Intent classification alone is T1, but the answer-extraction step requires matching free-text replies to our specific questions by meaning — that's where mini could under-extract and starve the outcome scorer. Validate on a back-test of ~100 historical replies before downgrading.

---

### [6] scout_vetting_service.py:712 — vetting_outcome_assessor
- **Current model**: gpt-5.4
- **Function/method**: `_generate_outcome` — final qualified/not_qualified verdict, score 0-100, and 2-3 sentence summary that becomes the Bullhorn note and recruiter handoff email.
- **Prompt complexity**: Large (2000–8000 tokens). Full questions + answers + conversation history + scoring rubric + decision rules.
- **Output structure**: JSON: recommendation label + integer score + free-text summary.
- **Recruiter visibility**: HIGH — summary is the body of the recruiter handoff email and the Bullhorn note action label.
- **Reasoning tier**: T3 — multi-factor synthesis with dollar-impact (qualified candidates get advanced; not_qualified get dropped).
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: This is effectively the Scout Vetting verdict — the brain of the vetting product. Downgrading risks both false-qualifies (waste recruiter time) and false-disqualifies (lose candidates). Per the audit constraints, do not downgrade core verdict calls.

---

### [7] scout_vetting_service.py:1056 — vetting_followup_email_writer
- **Current model**: gpt-5.4
- **Function/method**: `_generate_followup_reply` — composes the conversational HTML follow-up email to a candidate who partially answered, weaving in remaining questions and answering any candidate questions back.
- **Prompt complexity**: Medium (500–2000 tokens). Persona rules + already-answered list + candidate's questions + remaining questions + tone instructions.
- **Output structure**: HTML body content (free text, styled <div>).
- **Recruiter visibility**: HIGH — sent directly to the candidate as the "Scout by Myticas" recruiter.
- **Reasoning tier**: T3 — open-ended brand-voice writing that must avoid repeating answered questions and tactfully decline to answer questions outside scope.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: This is the candidate-facing brand voice. Tone, warmth, and avoiding the "robotic" failure mode all matter. Mini-generated emails would be noticeably more generic and risk hurting candidate experience and brand perception.

---

### [8] screening/prompt_builder.py:126 — years_experience_recheck
- **Current model**: gpt-5.4
- **Function/method**: `_recheck_years_experience` — re-counts months of experience per skill using exact arithmetic on resume dates, validated against the original year estimate (only used if delta ≥ 0.5yr).
- **Prompt complexity**: Large (2000–8000 tokens). Resume text up to 8000 chars + skills list + arithmetic formula.
- **Output structure**: JSON object keyed by skill with required_years, estimated_years, meets_requirement boolean, calculation string.
- **Recruiter visibility**: LOW — only used to override the original years_analysis; downstream feeds back into the verdict, which is itself flagship-generated.
- **Reasoning tier**: T1 — explicit arithmetic following a deterministic formula. The system prompt is literally "you are a precise arithmetic calculator".
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: Arithmetic on dates is exactly what mini handles cleanly with a clear formula. The downstream gating ("only override if delta ≥ 0.5") protects against any noise. High call frequency × clear T1 task = strong cost win.

---

### [9] screening/prompt_builder.py:224 — extract_job_requirements
- **Current model**: gpt-5.4
- **Function/method**: `extract_job_requirements` — extracts EXACTLY 5–7 mandatory requirements from a job description, with strong anti-hallucination rules.
- **Prompt complexity**: Medium (500–2000 tokens). Job title + description (capped 6000 chars) + extraction rules.
- **Output structure**: Free-text bullet list.
- **Recruiter visibility**: HIGH — requirements are shown to recruiters in the screening UI and are also fed back into the main vetting prompt as MANDATORY criteria.
- **Reasoning tier**: T3 — judgment about which requirements are "mandatory vs. nice-to-have", consolidating duplicates, refusing to invent year-of-experience numbers. Mistakes propagate into every vetting decision for that job.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Requirements extraction is high-leverage: every candidate scored for that job inherits these requirements. A mini hallucinating a "5+ years" requirement that's not in the JD would silently bias hundreds of vetting decisions. Keep flagship.

---

### [10] screening/prompt_builder.py:306 — zero_score_reverification
- **Current model**: gpt-5.4
- **Function/method**: `_reverify_zero_score` — second-pass check on candidates who scored 0% to catch false negatives.
- **Prompt complexity**: Medium (500–2000 tokens). Job title + truncated description + global requirements + prior summary/gaps + first 3000 chars of resume.
- **Output structure**: JSON: revised_score (int) + revised_summary + revised_gaps + revision_reason + confidence_reason.
- **Recruiter visibility**: MEDIUM — only revises the score when there's clear evidence; the revision is logged.
- **Reasoning tier**: T2 — bounded "did the first pass miss something obvious?" check with explicit "only revise upward when there is clear resume evidence".
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: This is a guarded second-opinion call; mini may be sufficient because the first-pass flagship already scored 0 and the reverify only flips with clear evidence. But because it can rescue otherwise-discarded candidates, A/B test on a backfill of zero-score logs and confirm rescue rate is preserved before flipping.

---

### [11] screening/prompt_builder.py:446 — main_vetting_verdict (CORE)
- **Current model**: `self.model` (configured flagship — gpt-5.4)
- **Function/method**: `analyze_candidate_job_match` — the primary candidate×job match analysis: score 0-100, summary, skills/experience match, gaps, location reasoning, prestige adjustments.
- **Prompt complexity**: xlarge (8000+ tokens). Up to 20k chars resume + 4k chars JD + custom requirements + global requirements + location instructions + system message.
- **Output structure**: JSON with match_score, match_summary, skills_match, experience_match, gaps_identified, key_requirements, etc.
- **Recruiter visibility**: HIGH — this score and summary appear directly on the recruiter dashboard; they drive every shortlist decision.
- **Reasoning tier**: T3 — open-ended multi-factor reasoning with dollar impact on every placement decision.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Per audit constraints, the core vetting verdict must not be downgraded. This is the brain of the product. Any quality regression here would cost more in missed placements than the entire savings from all other downgrades combined.

---

### [12] scout_support/conversation.py:440 — reopen_ticket_analysis
- **Current model**: gpt-5.4
- **Function/method**: `_analyze_reopen_with_history` — when a closed ticket is reopened, decides whether to handle directly via Bullhorn API actions, answer from history, propose a new solution, or escalate.
- **Prompt complexity**: Large (2000–8000 tokens). Up to 6000 chars of full ticket history + new user message + optional 2000 chars of attachment content.
- **Output structure**: JSON: can_handle_directly + response_to_user + needs_new_solution + proposed_solution object + escalation_reason + updated_understanding.
- **Recruiter visibility**: HIGH — output drives a live Bullhorn API action OR a user-facing reply OR an admin escalation; dollar-impact path.
- **Reasoning tier**: T3 — open-ended Scout Support reasoning that synthesizes prior failure history and proposes new solution steps.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Scout Support proposal generation is recruiter-/admin-facing and triggers real ATS mutations. Per audit constraints, be conservative on Scout Support — keep flagship for solution-proposal calls.

---

### [13] scout_support/conversation.py:633 — platform_followup_reply
- **Current model**: gpt-5.4
- **Function/method**: `_handle_platform_reply` (or similar follow-up handler) — generates the conversational reply when a user follows up on a PLATFORM feedback ticket (NOT an ATS support ticket — explicitly forbidden from proposing Bullhorn actions).
- **Prompt complexity**: Medium (500–2000 tokens). Ticket subject/description + last 10 conversation turns + user reply + optional attachment text.
- **Output structure**: JSON: response (plain-language reply) + needs_more_info + follow_up_question + can_close + close_reason.
- **Recruiter visibility**: HIGH — reply is sent directly to the platform user.
- **Reasoning tier**: T2 — bounded conversational reply for platform feedback (no ATS actions allowed); essentially acknowledgment writing.
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: This is a much narrower task than the ATS Scout Support proposals — explicitly NO Bullhorn actions, just acknowledgment + clarifying question + close decision. Mini likely sufficient, but it is user-facing so validate tone on a sample of historical platform tickets before flipping.

---

### [14] scout_support/conversation.py:852 — admin_question_response
- **Current model**: gpt-5.4
- **Function/method**: `_handle_admin_question` — generates a thorough technical answer when the admin asks a question while reviewing a ticket for approval.
- **Prompt complexity**: Large (2000–8000 tokens). Full ticket details + AI understanding + proposed solution + execution steps + conversation history + retrieved knowledge context + admin question.
- **Output structure**: Free-text answer addressed to the admin (technical depth expected).
- **Recruiter visibility**: HIGH — admin reads this verbatim; informs whether they approve a real ATS mutation.
- **Reasoning tier**: T3 — open-ended technical reasoning over Bullhorn entity details, validation rules, alternative approaches.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Admin uses these answers to make approve/reject decisions on real Bullhorn mutations. Quality of technical reasoning here directly affects whether bad solutions get approved. Keep flagship.

---

### [15] scout_support/conversation.py:972 — admin_user_intent_classifier
- **Current model**: gpt-5.4
- **Function/method**: `_ai_classify_response` — classifies an admin or user reply into a small closed label set (admin: approved/hold/closed/admin_question; user: approved/rejected/needs_changes).
- **Prompt complexity**: Small (<500 tokens), capped at 20 output tokens. The reply text (max 3000 chars) + label definitions.
- **Output structure**: Single classification label (one word).
- **Recruiter visibility**: LOW — drives a state-machine branch; there's also a keyword-based fallback classifier behind it.
- **Reasoning tier**: T1 — closed-set classification of email intent with deterministic options.
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: Pure closed-label classification with a keyword fallback safety net. mini handles intent classification at parity with flagship for this kind of bounded label set. High-frequency call (every reply), strong cost win, near-zero risk.

---

### [16] scout_support/conversation.py:1069 — admin_instruction_refinement
- **Current model**: gpt-5.4
- **Function/method**: `_refine_execution_with_admin_instructions` — when the admin approves but adds instructions, rewrites the execution_steps array to incorporate the admin's conditions (e.g., adds get_entity check + conditional update with runtime context placeholders).
- **Prompt complexity**: Medium-large (1500–4000 tokens). Original ticket + AI understanding + original execution steps JSON + admin's approval message + step-format documentation.
- **Output structure**: JSON: execution_steps array + description_user + description_admin.
- **Recruiter visibility**: HIGH — the rewritten steps are what actually executes against Bullhorn; errors here cause data corruption.
- **Reasoning tier**: T3 — composes valid Bullhorn API step graphs, including conditional logic via runtime context placeholders. Schema correctness directly affects production mutations.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Generating correct multi-step Bullhorn execution plans with conditional/runtime references is exactly the kind of structured reasoning where mini is more likely to drift on the placeholder syntax (`{{EntityType_id_field}}`) and produce broken automations. Keep flagship.

---

### [17] scout_support/conversation.py:1126 — admin_handling_intent_classifier
- **Current model**: gpt-5.4
- **Function/method**: `_classify_admin_handling_intent` — classifies admin's message in admin_handling status as either `ai_instruction` (the admin wants Scout to draft something) or `direct_reply` (forward to user).
- **Prompt complexity**: Small (<500 tokens), capped at 20 output tokens. Message text up to 3000 chars + 2-label definitions.
- **Output structure**: Single label (`ai_instruction` or `direct_reply`).
- **Recruiter visibility**: LOW — branches to either draft generation or email forwarding; keyword fallback exists.
- **Reasoning tier**: T1 — binary closed-set intent classification with keyword fallback.
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: 2-label classification with a keyword safety net is textbook mini territory. Cost ~10% of flagship with no realistic accuracy delta on a binary intent task.

---

### [18] scout_support/conversation.py:1242 — admin_draft_generator
- **Current model**: gpt-5.4
- **Function/method**: `_generate_admin_draft` — generates ad-hoc content (drafts, summaries, reports, escalation emails) when the admin instructs Scout Support directly.
- **Prompt complexity**: Large (2000–8000 tokens). Admin instruction + ticket subject/description (3000) + AI understanding (2000) + escalation reason + conversation history (4000) + attachment context.
- **Output structure**: Free-text draft (no JSON wrapper) — admin copies and uses directly.
- **Recruiter visibility**: HIGH — admin uses output verbatim to email users, third parties (e.g., Bullhorn Support), or internal stakeholders.
- **Reasoning tier**: T3 — open-ended professional writing with broad context synthesis.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: This is the admin's "AI assistant" drafting on their behalf — output goes out under the admin's name to external parties. Quality, tone, and context-handling all matter. Mini drafts would feel noticeably more generic and admins would stop trusting the feature.

---

### [19] scout_support/ai_analysis.py:201 — initial_ticket_understanding_and_solution
- **Current model**: gpt-5.4
- **Function/method**: `_generate_ai_understanding` — for a NEW Scout Support ticket, generates the AI's understanding of the issue + clarification questions OR a full proposed solution with execution_steps array (the 17+ Bullhorn action types).
- **Prompt complexity**: xlarge (8000+ tokens). Ticket details + category instructions + the full action-type catalog (~20 step types with examples) + entity-type list + diagnostic-step guidance.
- **Output structure**: JSON: understanding + clarification_questions + confidence + proposed_solution {description_user, description_admin, execution_steps[], resolution_type, underlying_concerns_*}.
- **Recruiter visibility**: HIGH — output drives the user-facing acknowledgment, the admin approval request, and the eventual ATS execution.
- **Reasoning tier**: T3 — open-ended diagnostic reasoning + correct construction of a multi-step Bullhorn action plan; the central brain of Scout Support.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: This is the Scout Support equivalent of the vetting verdict — every downstream action (clarification email, admin email, real Bullhorn mutation) cascades from this one call. Per audit constraints, be conservative on Scout Support; keep flagship.

---

### [20] scout_support/ai_analysis.py:421 — clarification_reply_analysis
- **Current model**: gpt-5.4
- **Function/method**: `_analyze_clarification` — re-runs Scout Support analysis after a user clarifies their original ticket, deciding whether enough info is now present to propose a solution or whether more questions are needed.
- **Prompt complexity**: xlarge (8000+ tokens). Same full action-type catalog as the initial analysis + ticket history + new clarification text.
- **Output structure**: JSON: answers_extracted + genuinely_unanswered + fully_understood + (potentially) full proposed_solution with execution_steps.
- **Recruiter visibility**: HIGH — same downstream as #19; can produce a live Bullhorn execution plan.
- **Reasoning tier**: T3 — same as #19, with the added step of integrating the clarification into a coherent updated solution.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Symmetric with the initial understanding call — produces real ATS execution plans and the same dollar-impact surface. Keep flagship.

---

### [21] scout_support/ai_analysis.py:565 — retry_alternative_strategy
- **Current model**: gpt-5.4
- **Function/method**: `_generate_retry_analysis` — when a Scout Support execution fails, analyzes the failure (which steps succeeded vs failed, diagnostic data) and proposes an ALTERNATIVE execution strategy.
- **Prompt complexity**: xlarge (8000+ tokens). Full ticket context + previous solution + per-step success/failure JSON (3000+3000+4000 char caps) + full attempt history + action-type catalog.
- **Output structure**: JSON: failure_analysis + alternative_strategy + can_retry + cannot_retry_reason + new proposed_solution + new execution_steps + resolution_type.
- **Recruiter visibility**: HIGH — drives the second/third execution attempt against Bullhorn.
- **Reasoning tier**: T3 — root-cause reasoning over Bullhorn API failure modes + composing an explicitly different execution plan.
- **Recommendation**: KEEP_FLAGSHIP
- **Rationale**: Failure analysis requires reasoning about WHY the original API call failed (permissions? validation rule? wrong entity?) and constructing a meaningfully different approach — exactly the open-ended reasoning where flagship dominates. Mini would tend to retry the same approach with cosmetic changes.

---

### [22] scout_support/ai_analysis.py:760 — attachment_image_vision
- **Current model**: gpt-5 / gpt-5.4 (vision call, tries gpt-5 first then falls back to gpt-5.4)
- **Function/method**: `_describe_image` — extracts text/details from screenshots attached to support tickets (error messages, field values, record IDs).
- **Prompt complexity**: Small text portion (<500 tokens) + one high-detail image. Vision token cost dominates.
- **Output structure**: Free-text description focused on diagnostic details.
- **Recruiter visibility**: MEDIUM — output is fed back into the Scout Support understanding prompt; not directly user-visible but shapes the resulting proposal.
- **Reasoning tier**: T1 — OCR + factual description, similar in nature to the resume vision-OCR calls already on mini at vetting/resume_utils.py:149/253.
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: This is OCR/visual-extraction with a "be specific and factual" instruction — same shape as the resume OCR calls that already use gpt-4.1-mini successfully. Vision input cost is significant per call, so the mini downgrade compounds. Note: gpt-4.1-mini supports vision; verify the SDK call signature works (image_url + base64) before flipping.

---

### [23] scout_support/knowledge.py:265 — failure_lesson_extractor
- **Current model**: gpt-4.1-mini (already mini)
- **Function/method**: `_generate_ai_failure_analysis` — distills an escalated/failed ticket into a 3-6 sentence lesson the AI should remember for similar future tickets.
- **Prompt complexity**: Large (~6000 chars input). Escalation summary + admin replies + conversation history.
- **Output structure**: Free-text lesson (3-6 sentences in imperative style).
- **Recruiter visibility**: LOW — stored in knowledge base for future RAG retrieval.
- **Reasoning tier**: T1 — summarization with a clear template.
- **Recommendation**: KEEP_FLAGSHIP (already mini — no action)
- **Rationale**: Already on mini. Correctly placed at this tier — summarization with a structured template is exactly what mini handles well. No change needed.

---

### [24] scout_support/knowledge.py:620 — knowledge_chunk_embedding
- **Current model**: text-embedding-3-large
- **Function/method**: `_generate_embedding` — embeds a text chunk (up to 30k chars) for the Scout Support knowledge base, used in RAG retrieval.
- **Prompt complexity**: N/A (embedding call).
- **Output structure**: 3072-dim float vector.
- **Recruiter visibility**: LOW — embeddings power retrieval; not directly visible.
- **Reasoning tier**: N/A — embedding model.
- **Recommendation**: KEEP_FLAGSHIP (no chat-model swap applies)
- **Rationale**: Embedding model choice is a separate axis from the chat-model question. text-embedding-3-large is the higher-quality option; downgrading to text-embedding-3-small would cut embedding cost ~80% but reduce retrieval quality. Embedding cost is typically <2% of total OpenAI spend, so unlikely to be the lever — leave alone unless a separate embedding-cost audit justifies revisiting.

---

### [25] scout_support_service.py:365 — platform_ticket_understanding
- **Current model**: gpt-5.4
- **Function/method**: `_generate_platform_understanding` — for a PLATFORM feedback ticket (NOT ATS), produces a one-paragraph confirmation of what the user is asking for + optional clarification questions.
- **Prompt complexity**: Small (<500 tokens). Category + subject + user message; capped at 500 output tokens.
- **Output structure**: JSON: understanding + clarification_needed + clarification_questions + is_platform_ticket flag.
- **Recruiter visibility**: MEDIUM — understanding is echoed back to the user in the acknowledgment email.
- **Reasoning tier**: T1 — short summarization + 2-question clarifier generation. No ATS action plan involved (platform tickets explicitly cannot trigger Bullhorn actions).
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: Tiny prompt, tiny output, no ATS execution risk, no recruiter-facing dollar impact. mini will produce equivalent acknowledgment summaries. Easy win.

---

### [26] routes/scout_screening.py:454 — requirements_optimizer
- **Current model**: gpt-5.4
- **Function/method**: `optimize_job_requirements` route — rewrites a recruiter's raw editable requirements into clear, testable, machine-readable screening criteria using prompt-engineering best practices.
- **Prompt complexity**: Medium (500–2000 tokens). System prompt with 9 rewriting rules + raw requirements text; capped 1500 output tokens.
- **Output structure**: Free-text optimized requirements (no markdown, no commentary).
- **Recruiter visibility**: HIGH — rewritten text is shown to the recruiter who then accepts/edits before saving as the screening requirements (which then feed into the main vetting prompt).
- **Reasoning tier**: T2 — bounded rewriting task with clear style rules; the recruiter reviews before saving.
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: Mini can apply the 9 rewriting rules competently, but because the output becomes the actual vetting criteria for that job, an unfaithful rewrite (drops a requirement, adds OR-clause incorrectly) would silently bias every match. Run side-by-side on ~20 sets and have a recruiter pick the better rewrite blind before flipping.

---

### [27] email_inbound_service/ai_mixin.py:67 — ai_resume_parser
- **Current model**: gpt-5.4
- **Function/method**: `_parse_resume_with_ai` — extracts structured fields (first/last name, email, phone, skills, education, work_history, summary) from raw resume text.
- **Prompt complexity**: Large (truncated to 20000 chars resume + JSON schema + name-extraction rules). 4000 output tokens.
- **Output structure**: JSON: name fields + contact + skills array + education objects + work_history objects + summary.
- **Recruiter visibility**: MEDIUM — extracted fields populate the Bullhorn candidate record (occupation, skills, contact, work history). Visible in candidate profile but validated downstream by the duplicate-check and vetting flows.
- **Reasoning tier**: T1 — structured-field extraction with explicit schema and well-defined name-validation rules (with deterministic blocklist fallback `is_valid_name`).
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: Schema-driven resume parsing is a textbook T1 task. There is already an `is_valid_name` validator that catches mini's typical name-extraction failure modes (citizenship strings, generic words). Very high call frequency × clear T1 = strongest single cost lever in the codebase.

---

### [28] email_inbound_service/ai_mixin.py:160 — last_resort_identity_extractor
- **Current model**: gpt-4.1-mini (already mini)
- **Function/method**: `_last_resort_ai_extraction` — focused fallback that extracts (name, email, phone) from email signals when all other parsers (subject regex, AI resume parser, filename, email-local-part) failed.
- **Prompt complexity**: Small-medium. Email subject + sender + filename + resume preview + body preview.
- **Output structure**: Strict JSON: first_name, last_name, email, phone (each nullable).
- **Recruiter visibility**: LOW — last-resort fallback; nulls are preferred over guesses.
- **Reasoning tier**: T1.
- **Recommendation**: KEEP_FLAGSHIP (already mini — no action)
- **Rationale**: Already correctly on mini. Tight prompt, strict JSON, 15s timeout, prefers null over guessing. No change needed.

---

### [29] email_inbound_service/ai_mixin.py:311 — ai_duplicate_validation
- **Current model**: gpt-5.4
- **Function/method**: `_ai_validate_duplicate` — when a name match is found in Bullhorn, asks AI for a 0.0–1.0 probability that the new applicant is the same person as the existing record.
- **Prompt complexity**: Small (<500 tokens). Two short profiles (name/email/phone/location); capped at 10 output tokens.
- **Output structure**: A single decimal number 0.0–1.0.
- **Recruiter visibility**: LOW — score is thresholded internally (≥0.7 = duplicate); validated by downstream duplicate-merge logic.
- **Reasoning tier**: T2 — bounded similarity judgment with edge cases (nicknames, spelling variations, partial contact overlap).
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: Duplicate confidence scoring with edge cases is the canonical T2 example in the audit guide. Mini can probably handle it but mistakes here cause either duplicate candidate records (recruiter annoyance) or wrong merges (data loss). A/B test on a backfill of name-matched pairs before flipping.

---

### [30] resume_parser.py:230 — pdf_to_html_formatter
- **Current model**: gpt-5.4
- **Function/method**: `_format_resume_html` — converts raw extracted resume text into clean structured HTML for display in the web UI (preserves all content, adds spacing, wraps headings/lists).
- **Prompt complexity**: Large (up to 20000 chars input). 8000 output tokens.
- **Output structure**: Free-text HTML body.
- **Recruiter visibility**: MEDIUM — HTML is displayed in the candidate resume pane; recruiters see formatting but content is preserved verbatim.
- **Reasoning tier**: T1 — pure formatting/markup task with explicit "don't add or summarize" instruction.
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: HTML structuring with a "preserve all content exactly" constraint is mechanical reformatting, not reasoning. Mini handles HTML structuring well at any size. High token volume per call (20k input, 8k output) makes the cost delta substantial.

---

### [31] automation_service/resume_mixin.py:720 — title_extractor_from_resume
- **Current model**: gpt-5.4
- **Function/method**: `_extract_title_from_resume_text` — extracts the candidate's most-recent job title from the first 6000 chars of resume text. Returns the title string or "NONE".
- **Prompt complexity**: Medium (500–2000 tokens — the truncated resume dominates). 500 output tokens.
- **Output structure**: Single short string (job title) or `NONE`.
- **Recruiter visibility**: MEDIUM — populates the candidate's `occupation` field in Bullhorn. Validated (length 2–100, "NONE" → null).
- **Reasoning tier**: T1 — single-field extraction with clear fallback. Borderline T2 because resumes can be messy and the "most recent" judgment requires light date reasoning, but the validator catches obviously-wrong outputs.
- **Recommendation**: DOWNGRADE_MINI
- **Rationale**: Extracting the most recent job title is a targeted parse with a length validator. Mini handles single-field extraction at parity. The `NONE` fallback + length check provide safety. Easy cost win.

---

### [32] vetting/resume_utils.py:149 — vision_ocr_raw_file
- **Current model**: gpt-4.1-mini (already mini)
- **Function/method**: `_ocr_raw_file_with_vision` — last-resort vision OCR for unreadable docs (sends raw bytes as image).
- **Prompt complexity**: Small text + one base64 document; 4000 output tokens.
- **Output structure**: Plain extracted text (verbatim, no summarization).
- **Recruiter visibility**: LOW — feeds extracted text into the resume-parsing pipeline.
- **Reasoning tier**: T1 — pure OCR.
- **Recommendation**: KEEP_FLAGSHIP (already mini — no action)
- **Rationale**: Already on mini. Correct tier — OCR is the canonical extract-no-judgment task. No change.

---

### [33] vetting/resume_utils.py:253 — vision_ocr_pdf_pages
- **Current model**: gpt-4.1-mini (already mini)
- **Function/method**: `_ocr_pdf_with_vision` — vision OCR over up to 5 PDF page images for image-based/scanned PDFs.
- **Prompt complexity**: Small text + up to 5 high-detail page images; 4000 output tokens.
- **Output structure**: Plain extracted text.
- **Recruiter visibility**: LOW — feeds the standard resume parsing pipeline.
- **Reasoning tier**: T1.
- **Recommendation**: KEEP_FLAGSHIP (already mini — no action)
- **Rationale**: Already on mini. Correct tier. No change.

---

### [34] fuzzy_duplicate_matcher.py:640 — fuzzy_dup_score_strict_json
- **Current model**: gpt-5.4 (configurable via `model_chat`, default `'gpt-5.4'`); uses response_format=json_object
- **Function/method**: `score_pair_with_ai` (primary path) — given two candidate profile texts (Layer A cosine-prefiltered), returns confidence 0.0–1.0 + reasoning that they are the same person.
- **Prompt complexity**: Medium-large (500–4000 tokens). Two candidate profile texts (name + work history + skills + location + education) + scoring rubric.
- **Output structure**: JSON: confidence (float) + reasoning (one short sentence).
- **Recruiter visibility**: LOW — confidence is thresholded at 0.90 before any merge proposal; merges go through the existing merge pipeline with additional safeguards.
- **Reasoning tier**: T2 — bounded duplicate-judgment task with edge cases (common name + common skills must be penalized; unique signals rewarded). The audit guide cites this exact pattern as the T2 archetype.
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: This is the canonical T2 case: the embedding pre-filter already narrows the candidate pool, and the 0.90 confidence threshold is conservative. Mini may well preserve precision/recall but the cost of a false-positive merge is high (data loss). A/B test on a labeled set of historical pairs before flipping; threshold can also be lifted to compensate.

---

### [35] fuzzy_duplicate_matcher.py:646 — fuzzy_dup_score_fallback
- **Current model**: gpt-5.4 (same `model_chat`)
- **Function/method**: `score_pair_with_ai` (fallback path when the SDK rejects `response_format=json_object`).
- **Prompt complexity**: Same as #34.
- **Output structure**: Same as #34 (parsed manually, with markdown-fence stripping).
- **Recruiter visibility**: LOW (same as #34).
- **Reasoning tier**: T2 (same).
- **Recommendation**: NEEDS_AB_TEST
- **Rationale**: Same call, same prompt, just the no-json-format fallback. Treat as one A/B test — both branches use `self.model_chat` and will flip together.

---

### [36] embedding_service.py:194 — resume_and_jd_embedding
- **Current model**: text-embedding-3-large
- **Function/method**: `generate_embedding` — produces the 3072-dim embedding for resume text or job descriptions used by the Layer 1 cosine pre-filter.
- **Prompt complexity**: N/A (embedding call, max 8000 tokens with intelligent head/tail truncation).
- **Output structure**: 3072-dim float vector.
- **Recruiter visibility**: LOW — drives the cosine pre-filter that decides which jobs reach the expensive vetting layer.
- **Reasoning tier**: N/A — embedding model.
- **Recommendation**: KEEP_FLAGSHIP (no chat-model swap applies)
- **Rationale**: Same as #24. Embedding model choice is a separate cost axis from chat-model selection, and embedding cost is typically a small fraction of total spend. The Layer 1 pre-filter quality directly affects how many irrelevant pairs reach the flagship vetting call, so degrading embedding quality could *increase* total cost. Out of scope for this chat-model audit.

---

## Summary index by recommendation

**DOWNGRADE_MINI (high-confidence, low-risk wins):**
- [3] job_classification_service.py:121 — closed-taxonomy classifier
- [8] screening/prompt_builder.py:126 — years arithmetic recheck (validated downstream)
- [15] scout_support/conversation.py:972 — admin/user intent classifier (closed labels + keyword fallback)
- [17] scout_support/conversation.py:1126 — ai_instruction vs direct_reply binary classifier
- [22] scout_support/ai_analysis.py:760 — attachment image vision OCR (verify mini vision works first)
- [25] scout_support_service.py:365 — platform ticket understanding (no ATS action risk)
- [27] email_inbound_service/ai_mixin.py:67 — AI resume parser (largest volume win)
- [30] resume_parser.py:230 — PDF→HTML formatter
- [31] automation_service/resume_mixin.py:720 — title extractor

**NEEDS_AB_TEST (likely fine on mini, but recruiter-facing or risk warrants validation):**
- [2] scout_prospector_service.py:519 — ICP refinement
- [4] scout_vetting_service.py:326 — vetting question generation (candidate-facing tone)
- [5] scout_vetting_service.py:589 — reply intent + answer extraction
- [10] screening/prompt_builder.py:306 — zero-score reverification
- [13] scout_support/conversation.py:633 — platform follow-up reply
- [26] routes/scout_screening.py:454 — requirements optimizer
- [29] email_inbound_service/ai_mixin.py:311 — duplicate name validation
- [34] fuzzy_duplicate_matcher.py:640 — fuzzy duplicate scoring (json_object branch)
- [35] fuzzy_duplicate_matcher.py:646 — fuzzy duplicate scoring (fallback branch)

**KEEP_FLAGSHIP (open-ended reasoning, recruiter dollar-impact, or core verdicts):**
- [1] scout_prospector_service.py:233 — web-search prospect research
- [6] scout_vetting_service.py:712 — vetting outcome verdict (CORE)
- [7] scout_vetting_service.py:1056 — candidate-facing follow-up email
- [9] screening/prompt_builder.py:224 — job requirements extraction (cascades to every match)
- [11] screening/prompt_builder.py:446 — main vetting verdict (CORE — do not touch)
- [12] scout_support/conversation.py:440 — reopened-ticket analysis (proposes new solution)
- [14] scout_support/conversation.py:852 — admin question response (drives approve decisions)
- [16] scout_support/conversation.py:1069 — admin instruction → execution-step refinement
- [18] scout_support/conversation.py:1242 — admin draft generator (admin's voice to externals)
- [19] scout_support/ai_analysis.py:201 — initial Scout Support understanding + solution
- [20] scout_support/ai_analysis.py:421 — Scout Support clarification re-analysis
- [21] scout_support/ai_analysis.py:565 — Scout Support failure retry strategy

**Already mini (no action):**
- [23] scout_support/knowledge.py:265
- [28] email_inbound_service/ai_mixin.py:160
- [32] vetting/resume_utils.py:149
- [33] vetting/resume_utils.py:253

**Embedding calls (separate axis from chat-model audit):**
- [24] scout_support/knowledge.py:620
- [36] embedding_service.py:194
