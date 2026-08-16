# Phase 3 Step 2 — BM25 Retrieval Baseline

Measurement task per `PHASE3_PLAN.md` (Step 2 — Retrieval layer) and
`STEP2_FASTFOLLOW_HYBRID.md` (hybrid/RRF/reranking explicitly deferred).
Deliverable is a precision/recall report against `data/queries.json`, not a
production retrieval service. `data/queries.json` was not modified.

**Note on stale IDs:** the task brief referred to `data/eval/queries.json`
and named q20/q21 as the cross-lingual entries and q22–q24 as the true
negatives. The actual current file is `data/queries.json` (26 entries,
q01–q26); cross-lingual is **q22 (Spanish) / q23 (Czech)** and true-negative
is **q24–q26**. This report uses the current file as ground truth throughout.
Also: the brief's "expected arrays reference guest messages exclusively" and
"as many as 9 expected messages" don't quite hold — three `multi_turn`
entries include host messages (see Corpus section), and the largest expected
set is actually 10 (q03). Both are called out below since they affect the
results.

No PHASE3_HANDOFF.md was found in the repo; PHASE3_PLAN.md and
STEP2_FASTFOLLOW_HYBRID.md were sufficient to scope this task.

## Corpus

`data/hospitable.db`'s `messages` table holds **1,467** total messages
(796 host, 671 guest). Indexed here: **guest messages only**, per the task
brief's instruction — matches how `queries.json`'s `expected` arrays are
built for every type except `multi_turn`.

**Caveat found during review:** `multi_turn` entries (q18–q21) test
*thread summarization*, and three of the four have expected sets that
include host replies, because summarizing a negotiated arrangement
requires both sides of the exchange:

| id | total expected | guest (indexed) | host (excluded) |
|---|---|---|---|
| q18 | 9 | 5 | 4 |
| q19 | 5 | 3 | 2 |
| q20 | 5 | 2 | 3 |
| q21 | 4 | 4 | 0 |

For q18–q20, recall@k can **never reach 1.0** under a guest-only index —
the host-authored expected messages simply aren't in the corpus. This is a
corpus-construction artifact, not a retrieval failure, and is called out
per-entry below rather than folded silently into the average.

After filtering to `sender = 'guest' AND body` non-empty: **658 messages
indexed** (671 guest rows total; 13 dropped for empty/null body). Checked
individually rather than assumed uniform: 12 of the 13 have image
attachments (`body: null`, an `attachments` list with an image entry) — the
expected case. One, **message 1092**, has `body: null` **and** an empty
`attachments: []` — it isn't attachment-only, it's genuinely empty content
with no evident cause in the raw record.

### Tokenization

Moderate preprocessing: lowercase → extract `[a-z0-9]+` tokens (strips all
punctuation/emoji/symbols as a side effect of the character class) → drop
stopwords. Stopword list: **NLTK's built-in English list**
(`nltk.corpus.stopwords.words("english")`, downloaded via
`nltk.download("stopwords")`), 198 words as installed. No custom additions
or removals.

Before/after examples (from the actual corpus):

```
id=2   BEFORE: "Hello! I'm wanting to book your Airbnb for July 24–26. I'll be
               arriving in DC at 6:00 AM, so I was wondering if there's any
               chance I could do an early check-in. Thank you!"
       AFTER:  ['hello', 'wanting', 'book', 'airbnb', 'july', '24', '26',
                'arriving', 'dc', '6', '00', 'wondering', 'chance', 'could',
                'early', 'check', 'thank']

id=647 BEFORE: 'Hi Samir, I'm considering your place as a stay from Jan. 29 - 31.
               I have a car am I'm most concerned about parking. Are you able
               to secure a "guest" parking pass before my stay...'
       AFTER:  ['hi', 'samir', 'considering', 'place', 'stay', 'jan', '29',
                '31', 'car', 'concerned', 'parking', 'able', 'secure',
                'guest', 'parking', 'pass', 'stay', 'would', 'reserved',
                'first', 'get', 'best', 'jeanne']

id=1320 BEFORE: 'So the whole upstairs is ours?'
        AFTER:  ['whole', 'upstairs']
```

Function words, pronouns, and the like are removed correctly; short
messages can end up with very few surviving tokens (id=1320: 6 words → 2
tokens), which matters for BM25's ability to discriminate them.

### Cross-lingual handling (q22 Spanish, q23 Czech)

**No language-aware handling at all** — confirmed explicitly, as requested.
The stopword list is English-only, so no Spanish/Czech stopwords are
removed, and there is no translation step. Two distinct effects observed:

- **Spanish (q22, msg 321):** tokens mostly pass through as opaque, intact
  words, but **one does collide with the English stopword list and gets
  incorrectly stripped**: the source text is "...tienen secador de cabello
  **y** lavadora de ropa?" (cabello **and** lavadora), and NLTK's English
  list includes the single-letter word "y" (used in English as a
  contraction fragment) — actual output confirmed:
  `['buenos', 'dias', 'por', 'favor', 'una', 'consulta', 'en', 'los',
  'servicios', 'del', 'hospedaje', 'tienen', 'secador', 'de', 'cabello',
  'lavadora', 'de', 'ropa']` — "y" is missing between "cabello" and
  "lavadora." In this case the collision is harmless (a Spanish
  conjunction has no query-matching value either way, so the query's
  English tokens `['property', 'hair', 'dryer', 'washer', 'dryer']` still
  have zero overlap with the corrected or uncorrected token list) but it's
  a real, incidental side effect of using an English-only stopword list on
  non-English text, not a clean "pass-through as opaque tokens" story.
- **Czech (q23, msg 412):** actively **mangled**, not just opaque. The
  `[a-z0-9]+` regex is ASCII-only, so every accented character (í, ý, č,
  ř, ů, ...) acts as a token boundary. `"Jenom prosíme o druhý ručník,
  jsme dva 😇"` tokenizes to `['jenom', 'pros', 'druh', 'ru', 'n', 'k',
  'jsme', 'dva']` — `"prosíme"` → `pros`, `"ručník"` (the actual word for
  "towel", the thing the query is about) is shattered into `ru`, `n`, `k`.
  This is a worse failure mode than "opaque non-English tokens": the
  fragments are near-meaningless and could spuriously collide with unrelated
  short English tokens. Worth fixing before any real cross-lingual claim is
  made (Unicode-aware tokenization, e.g. `\w+` with `re.UNICODE` or a
  proper tokenizer), independent of the translation gap.

Both effects are exactly why BM25 is expected to do poorly on q22/q23 — see
results below.

**Known limitation, deferred (not fixed in this pass):** `_TOKEN_RE` in
`scripts/bm25_baseline.py` is `[a-z0-9]+`, ASCII-only, so accented Czech
characters aren't matched and act as token boundaries. This is a real
tokenization bug, confirmed at the token level, and it compounds with the
English-only stopword list rather than acting alone: `"prosíme"` first
splits into `['pros', 'me']` by the regex (the accented `í` breaks the
word), and *then* `'me'` is separately dropped because it happens to be an
English stopword — leaving only `pros` in the final output, for reasons
that have nothing to do with Czech grammar. Similarly `"druhý"` → `druh`
and `"ručník"` (the actual word for "towel," the referent of the query)
shatters into `ru`, `n`, `k`.

This is **not fixed here**, deliberately. The bug is separate from, and
compounds rather than causes, the underlying cross-lingual matching gap:
BM25 has no mechanism to match Czech tokens against an English query
regardless of how cleanly the Czech is tokenized, so q23's recall would
very likely still be 0.000 even with correct Czech tokenization. Fixing
tokenization now would not change this baseline's headline result, only
the intermediate token lists. Unicode-aware tokenization (e.g. `\w+` with
`re.UNICODE`, or a proper multilingual tokenizer) is worth doing once,
properly, rather than patching this single accented-character case —
revisit either when the embeddings phase needs correct Czech token/text
handling for a fair BM25-vs-embeddings comparison, or in a dedicated
tokenization-quality pass.

## Index build

`rank_bm25.BM25Okapi` over the 658 preprocessed guest documents, built once,
in memory:

- **Build time:** 5.8 ms
- **Memory:** 323.9 KiB current / 390.0 KiB peak (`tracemalloc`, measured
  around the `BM25Okapi(...)` call only)

Trivial on both counts, as expected for a corpus this size — stated rather
than assumed, per the brief.

## Eval harness and choice of k

**k = 15.**

Expected-set sizes in the current file range from 1 (many entries) to 10
(q03, parking). Justification:

- k needs headroom **above** the largest expected set (10) — setting k
  equal to the max would leave zero room for a realistic, imperfect
  ranking to still recover all 10; k=15 gives ~50% slack.
- k shouldn't be pushed much higher, because it directly determines
  precision@k's ceiling for the many entries with only 1–2 expected
  messages: even a perfect top-1 hit caps precision@15 at 1/15 ≈ 0.067 for
  a single-expected-message entry. This is a structural property of a
  fixed k against variable-size expected sets, not a BM25 weakness — it's
  called out explicitly per-entry below and **recall@k, not precision@k, is
  the metric to trust** for the 1–2-expected entries (13 of the 23
  non-true-negative entries). Precision@k is more informative for the
  handful of larger sets (q03, q11, q18).

For `true_negative` entries (q24–q26, `expected: []`), precision/recall are
undefined (no correct answer exists) — reported separately as the BM25
score of the top-ranked result, per the brief.

## Results

### Overall and by type

| type | n | avg precision@15 | avg recall@15 |
|---|---|---|---|
| lexical | 7 | 0.152 | 0.771 |
| paraphrase | 10 | 0.053 | 0.346 |
| multi_turn | 4 | 0.033 | 0.100 |
| cross_lingual | 2 | 0.000 | 0.000 |
| **overall** (excl. true_negative) | 23 | 0.075 | 0.403 |

### true_negative — top score check (q24–q26)

| id | query | top score | top message |
|---|---|---|---|
| q24 | pets/dogs allowed? | 4.71 | msg 1198, "Is the guest staying On our floor?" |
| q25 | smoke detector / fire safety? | 5.72 | msg 1233, "...had to put on all my clothes not to freeze..." |
| q26 | pool at the property? | 4.71 | msg 1198, "Is the guest staying On our floor?" |

All three top-ranked matches are incidental generic-word overlap ("guest"),
not topical matches — there is no message about pets, smoke detectors, or a
pool in the corpus, and BM25 correctly fails to invent one. Scores (4.71,
5.72) sit well below genuine matches elsewhere (e.g. q13's 22.49, q20's
15.22) — **behaving as expected**: a well-behaved retriever should show
low scores here relative to real hits, and it does, though "low" is
relative, not near-zero in absolute terms (BM25 always returns some score
for any nonempty query/doc overlap). Note q24 and q26 land on the *exact
same* top document — both queries are short and share only common,
low-information tokens with 1198, which happens to score highest against
both by coincidence, not by any semantic property of 1198.

### Per-entry detail

| id | type | P@15 | R@15 | n_expected | top score |
|---|---|---|---|---|---|
| q01 | lexical | 0.133 | 0.667 | 3 | 6.76 |
| q02 | lexical | 0.067 | 1.000 | 1 | 11.14 |
| q03 | lexical | 0.267 | 0.400 | 10 | 6.36 |
| q04 | lexical | 0.333 | 1.000 | 5 | 13.27 |
| q05 | lexical | 0.067 | 0.333 | 3 | 10.30 |
| q06 | lexical | 0.067 | 1.000 | 1 | 10.25 |
| q07 | lexical | 0.133 | 1.000 | 2 | 7.81 |
| q08 | paraphrase | 0.067 | 0.500 | 2 | 9.54 |
| q09 | paraphrase | 0.133 | 0.667 | 3 | 8.20 |
| q10 | paraphrase | 0.133 | 0.667 | 3 | 11.39 |
| q11 | paraphrase | 0.067 | 0.125 | 8 | 7.00 |
| q12 | paraphrase | 0.000 | 0.000 | 3 | 7.10 |
| q13 | paraphrase | 0.067 | 1.000 | 1 | 22.49 |
| q14 | paraphrase | 0.000 | 0.000 | 1 | 6.84 |
| q15 | paraphrase | 0.000 | 0.000 | 2 | 8.37 |
| q16 | paraphrase | 0.000 | 0.000 | 2 | 8.16 |
| q17 | paraphrase | 0.067 | 0.500 | 2 | 9.15 |
| q18 | multi_turn | 0.000 | 0.000 | 9 (5 indexable) | 5.05 |
| q19 | multi_turn | 0.067 | 0.200 | 5 (3 indexable) | 10.60 |
| q20 | multi_turn | 0.067 | 0.200 | 5 (2 indexable) | 15.22 |
| q21 | multi_turn | 0.000 | 0.000 | 4 (4 indexable) | 8.27 |
| q22 | cross_lingual | 0.000 | 0.000 | 1 | 3.17 |
| q23 | cross_lingual | 0.000 | 0.000 | 1 | 7.80 |

## Per-type commentary

**lexical (avg recall 0.771)** — the strongest type by far, as designed:
these queries share verbatim vocabulary with the source messages, and BM25
is a pure keyword-overlap scorer, so this is close to its best case. 5 of
7 entries hit recall=1.0 (q02, q04, q06, q07, plus near-misses). The
weak spot is q03 (recall 0.4, 10 expected messages spread across a 30-
message thread) — some of those 10 were selected for *thematic* relevance
to "parking" over a long conversation rather than tight lexical overlap
with the query itself, so not every one is actually a lexical match at the
single-message level; expected, not a BM25 bug.

**paraphrase (avg recall 0.346)** — the intended stress test: does the
retriever find the message when wording *doesn't* match. Results are
genuinely mixed, which is the useful signal. q13 hits recall=1.0
(top score 22.49, the single highest score in the whole run) because its
one expected message happens to still share strong distinctive vocabulary
with the query despite being a paraphrase. q11 (the 6-incident,
8-message access-failure entry) is the weakest at recall=0.125 — each
guest phrased "I can't get in" differently enough (code/lock/door wording
varies per incident) that BM25 only catches the one closest to the query's
literal phrasing, missing the other five incidents entirely; this is
exactly the retrieval gap this entry was built to expose. q12, q14, q15,
q16 hit recall=0.0 — worth a closer look before embeddings, since a
15-slot window returning nothing at all is a stronger miss than a partial
one.

**multi_turn (avg recall 0.100, worst of the non-true-negative types)** —
expected, for two independent reasons layered together:
1. **Structural cap**: q18–q20 can't reach full recall against a guest-only
   index (see Corpus caveat) — ceilings are 0.556 / 0.600 / 0.400
   respectively, not 1.0.
2. **Genuine retrieval failure on top of that**: even against those lower
   ceilings, actual recall (0.0 / 0.2 / 0.2) falls well short. q18 in
   particular retrieves *zero* of its 5 indexable expected messages in the
   top 15 (top score only 5.05, one of the lowest top-scores in the whole
   run) — a "summarize the guest-substitution issue" query has essentially
   no lexical overlap with the individual thread messages it should
   surface, since summarization queries describe an *outcome*, not any
   single utterance. **q21 is the cleanest failure in this set**: all 4
   expected messages are guest-authored (no structural cap at all), yet
   recall is 0.0 — see the dedicated callout below.

**cross_lingual (avg recall 0.000)** — performed exactly as expected and
exactly as designed to demonstrate: BM25 has no mechanism to bridge
English queries to Spanish/Czech message text, and scored 0/2. This is the
headline case for "why embeddings might be needed" — see Corpus section
above for the added Czech-specific tokenization-mangling finding.

**true_negative** — see dedicated section above; behaved correctly (low
relative scores, no fabricated match).

## Notable cases for the writeup

**Clearly succeeded:** q13 (bathroom private/shared confusion) — recall
1.0 with the highest top-score of the entire run (22.49) despite being
classified `paraphrase`. Its single expected message evidently retains
enough distinctive shared vocabulary with the query that the paraphrase
framing didn't actually cost BM25 anything here — a good concrete
counter-example to "BM25 always fails on paraphrases."

**Clearly failed:** q21 (kitchen equipment across a thread) — recall 0.0
with *zero structural excuse* (all 4 expected messages are guest-authored
and in the index). The query asks about "kitchen equipment or cooking
supplies" in the abstract; the actual messages ask for concrete items —
"pots and pans," "a baking sheet," "aluminum foil," "a meat thermometer."
Zero shared content words between the category-level query and any of the
item-level messages. This is the single cleanest illustration in the run
of the vocabulary-mismatch problem embeddings are meant to solve — worth
leading with in the blog writeup.

**Checked, not just assumed:** q12, q14, q15, q16 all return recall 0.0
with no structural excuse (guest-only expected sets). Read all four
query/message pairs directly rather than assuming it's generic
"paraphrase is hard" — each has a specific, real cause:

- **q12** ("bring a friend...not confirmed on") — the expected message
  does say "add a friend," sharing one real token, but it's an 80-word
  rambling message where that phrase is a small fraction of the token
  mass; BM25's length normalization plus 44 total tokens likely dilutes
  the one match below the top-15 cutoff.
- **q14** ("listing photos don't match") — the actual message says "pics,"
  never "photos," and never uses "listing" or "match" at all — genuine
  synonym gap, zero shared content words.
- **q15** ("thermostat...heating...switch over to cooling") — the actual
  messages say "heat"/"turn off"/"air," never "thermostat" or "cooling" —
  same synonym-gap pattern as q14.
- **q16** ("cancellation...rebook") — the actual message says "canceled"
  (verb, past tense) where the query says "cancellation" (noun). Same
  root, **zero token overlap**, because this preprocessing does no
  stemming/lemmatization. This is a real, fixable gap distinct from the
  others: q12/q14/q15 fail because the *concepts* are phrased with
  different words (a job for embeddings), but q16 fails on a pure
  morphological variant a stemmer would catch without needing semantic
  search at all. Worth noting for the writeup as a cheap improvement BM25
  itself could pick up before ever reaching for embeddings.

## Scope confirmation

- `data/queries.json` was not modified.
- No embeddings were built.
- No RRF, hybrid combination, or reranking was implemented — per
  `STEP2_FASTFOLLOW_HYBRID.md`, that's the explicit next step after this
  baseline is reviewed, not part of this task.
- Stopping here for review, as instructed.
