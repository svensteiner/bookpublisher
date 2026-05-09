SYSTEM_PROMPT = """You are a senior German nonfiction publisher, Amazon KDP conversion strategist, narrative editor, and AI-agent publishing reviewer.

You protect the author's voice. Do not create generic AI-business-book language. Do not over-polish. Do not add hype.

The book should feel practical, skeptical, operational, Austrian CFO/operator-like, anti-hype, and field-note based.

Think like an experienced major publishing-house editorial board and Kindle-native publishing team: acquisition editor, developmental editor, line editor, copy chief, production editor, ebook formatter, metadata specialist, sales director, bookseller, Amazon merchandiser, and launch publicist. Be rigorous, concrete, and commercially honest.

Output in German unless JSON is explicitly requested. Be direct, specific, and commercially useful. If evidence is missing, say what is missing instead of inventing it."""


MANUSCRIPT_SCORE_PROMPT = """Review this German nonfiction business manuscript for Amazon KDP as a major publishing-house gate review.

Score each area from 1 to 10:
- amazon_purchase_appeal
- opening_strength
- title_fit
- reader_promise
- differentiation
- pacing
- repetition_control
- credibility
- voice_consistency
- business_relevance
- structure_and_chapter_logic
- sample_page_pull
- nonfiction_argument_quality
- review_risk
- refund_risk
- kindle_sample_conversion
- ebook_readability

Return ONLY valid JSON:
{{
  "scores": {{
    "amazon_purchase_appeal": 1,
    "opening_strength": 1,
    "title_fit": 1,
    "reader_promise": 1,
    "differentiation": 1,
    "pacing": 1,
    "repetition_control": 1,
    "credibility": 1,
    "voice_consistency": 1,
    "business_relevance": 1,
    "structure_and_chapter_logic": 1,
    "sample_page_pull": 1,
    "nonfiction_argument_quality": 1,
    "review_risk": 1,
    "refund_risk": 1,
    "kindle_sample_conversion": 1,
    "ebook_readability": 1
  }},
  "final_score": 1,
  "verdict": "publish|revise|do_not_publish",
  "top_strengths": [],
  "top_risks": [],
  "top_fixes": [],
  "acquisition_note": "",
  "reader_positioning": "",
  "one_sentence_sales_handle": ""
}}

Rules:
- Do not reward polish if it weakens voice.
- Penalize vague promise, unsupported claims, repetitive proof, weak opening pages, unclear reader payoff, and Amazon mismatch.
- Treat "publish" as acceptable only if a paying reader would understand the promise before purchase and feel it was delivered.
- Judge Kindle commercial fitness separately from manuscript quality: opening sample pull, reflow-friendly structure, TOC clarity, skimmability, and whether the first 10% creates enough purchase intent.

Project metadata:
{metadata}

Manuscript:
{manuscript}
"""


PUBLISHER_BOARD_PROMPT = """Create a major publishing-house style editorial board report for this German nonfiction KDP book.

Role mix:
- acquiring editor
- developmental editor
- line editor
- copy chief
- production editor
- Kindle ebook formatter
- Amazon/KDP metadata lead
- sales and bookseller perspective
- launch publicist

Assess with sharp publishing judgment:
1. Acquisition verdict: why this book should or should not be published now.
2. Reader promise: exact target reader, purchase trigger, expected transformation, likely disappointment.
3. Positioning: title/subtitle logic, shelf/category fit, competitive angle, anti-hype differentiation.
4. Manuscript architecture: opening, chapter order, narrative escalation, repetition, chapter endings, practical payload.
5. Voice and line quality: where the voice is strongest, where it becomes generic, where it needs less polish.
6. Trust and legal/reputation risk: unsupported claims, earnings implications, AI overpromise, privacy, testimonials, risky wording.
7. Production readiness: front matter, back matter, TOC, formatting, print/Kindle friction, cover/package coherence.
8. Kindle ebook mechanics: reflow behavior, heading hierarchy, table/list risk, clickable TOC, front matter length, first 10% sample, device readability, and Kindle preview checks.
9. Commercial package: Amazon description, keywords, categories, pricing, sample pages, review risk.
10. Sellability plan: what must be true for strangers on Amazon to click, sample, buy, finish, and review positively.
11. Editorial intervention plan: exact next 10 fixes in priority order.
12. Final board decision: GO, GO_AFTER_FIXES, or HOLD, with conditions.

Constraints:
- German output.
- No motivational language.
- No invented market data.
- Be specific enough that an editor could execute the fixes.
- Preserve the author's Austrian CFO/operator field-note voice.

Context:
{context}
"""


VOICE_PROMPT = """Create a voice preservation report for this German manuscript.

Assess whether it still sounds like:
- Austrian CFO/operator
- practical
- skeptical
- anti-hype
- field-note style
- experienced builder

Flag:
- generic AI phrasing
- consultant language
- motivational language
- over-polished sections
- fake emotionality

Give exact examples and exact recommendations. Keep it concise.

Metadata:
{metadata}

Manuscript:
{manuscript}
"""


AMAZON_PROMPT = """Create an Amazon conversion review for this German KDP nonfiction business book.

Evaluate:
- title
- subtitle
- first 3 Amazon description lines
- full book description
- category fit
- keywords
- pricing perception
- reader targeting
- thumbnail readability if cover exists
- look-inside/sample-page conversion risk
- review-trigger risks after purchase
- mismatch between promise and manuscript delivery
- Kindle Unlimited/page-read potential, if relevant
- first 10% sample performance
- ebook formatting and navigation expectations
- buyer journey from thumbnail to Look Inside to purchase

Give specific improvements only. Do not hype. If metadata was found in supplemental editorial notes, use it as context but still verify whether the production package appears complete.

Metadata and discovered assets:
{metadata}

Available text:
{text}
"""


CHECKLIST_PROMPT = """Create a final KDP pre-publish checklist for this German nonfiction business book.

Checklist areas:
- manuscript ready?
- cover ready?
- metadata ready?
- description ready?
- author bio ready?
- categories ready?
- keywords ready?
- price recommendation?
- launch posts ready?
- risk flags?
- sample pages ready?
- front matter/back matter ready?
- rights/legal wording ready?
- post-purchase review risk acceptable?
- Kindle clickable TOC ready?
- Kindle Previewer/device QA ready?
- first 10% sample commercially strong?
- ebook reflow and mobile readability ready?
- Amazon product-page conversion ready?

Use status labels: READY, REVIEW, FIX.
End with a publish / do not publish recommendation.

Context:
{context}
"""


LAUNCH_PROMPT = """Generate launch assets in the author's voice for this German business nonfiction book.

Tone:
German. Sharp. Professional. No hype. No emojis. No generic AI phrases.
Preserve the author's practical, skeptical, CFO/operator voice.

Create:
- 5 LinkedIn posts
- 5 short X posts
- 3 newsletter teasers
- 1 Amazon description
- 1 back-cover text
- 1 author bio
- 7 Amazon keyword candidates
- 3 category-positioning notes
- 1 first-10-percent Kindle sample diagnosis
- 1 product-page conversion sequence from thumbnail to purchase
- 5 review-request lines that do not pressure readers

Context:
{context}
"""


SUMMARY_PROMPT = """Create a compact final executive publisher summary.

Include:
- current readiness
- biggest remaining risk
- top 5 fixes
- publish / do not publish recommendation

Keep it practical and concise.

Context:
{context}
"""
