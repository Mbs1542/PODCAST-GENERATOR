# M.B.S Studio: Competitive Landscape and Expert Recommendations

*Research date: 2026-11-10. Prepared as an independent competitive analysis of AI lecture-to-podcast and study tools, with Hebrew/RTL as the focus.*

> **How to read this report.** Every factual claim about a third party is tied to a numbered source in the [Sources](#sources) section. Figures marked **(unverified)** could not be confirmed against a primary source. Sources marked *(search snippet)* in the list were seen in search results but not opened in full. Where I estimate something myself (for example, per-episode cost), I say so. Prices and features in this market change monthly; re-check anything you plan to quote publicly.

---

## 1. Executive summary

**Where M.B.S Studio stands.** M.B.S Studio is a niche, vertically integrated **"Hebrew lecture → faithful study podcast"** pipeline that runs on the user's own machine. Feature for feature it overlaps heavily with Google NotebookLM, which Google renamed **Gemini Notebook** on 2026-07-16 [7]. NotebookLM is free, supports Hebrew [8][9], offers Deep Dive, Brief, Critique and Debate formats [1], and adds video, flashcards and quizzes [6]. M.B.S Studio cannot win on breadth or price. It can win on the parts of the job NotebookLM does not do, or does badly, for an Israeli student:

1. getting the lecture out of a login-protected course portal;
2. transcribing it with a Hebrew-specialist ASR model;
3. writing a podcast that is checked against the lecture, segment by segment;
4. letting the user edit the plan before anything is voiced;
5. keeping keys and recordings on the user's machine.

**Top 5 insights**

1. **Hebrew support alone no longer sets M.B.S Studio apart.** In 2025–2026 every major stack added Hebrew:
   - NotebookLM Audio Overviews (April 2025) [8][9];
   - ElevenLabs Eleven v3 and v4 [14];
   - Gemini 3.8 Flash TTS [18].

   On ivrit.ai's blind **Hebrew TTS Arena**, Gemini 3.1 Flash TTS (ELO 1585), Deepdub (1578) and ElevenLabs v3 (1546) lead. Vote counts are small, about 200 per model [22]. What sets M.B.S Studio apart is the pipeline around the voices, not the voices.

2. **Faithfulness is the biggest unmet need in the category, and M.B.S Studio already invests in it.**
   - A July 2026 arXiv study found that even GPT-4o "frequently generate[s] ungrounded content" in document-to-podcast transcripts. It proposes turn-level detection plus repair ("catch-n-repair") [42].
   - A peer-reviewed AGU paper turned three papers into NotebookLM Audio Overviews and found errors in every one, including invented extrapolations [11].
   - Google's own help page says Audio Overviews "may contain inaccuracies" [1].

   M.B.S Studio's per-segment grounding check is architecturally the same idea as catch-n-repair. Today it is invisible to the user and unmeasured. This is the single most valuable asset to productise.

3. **Format menus are now table stakes.**
   - NotebookLM ships Deep Dive, Brief, Critique and Debate, plus a focus prompt and three length settings [1].
   - ListenHub offers digest, deep-dive and debate (third-party listing) [32].
   - ElevenLabs' podcast API has short, default and long durations plus an instructions prompt [16].

   M.B.S Studio's seven formats are good, but they do not differentiate it. *Exam prep* is the only format with a clear education angle that competitors do not name.

4. **The market moved from "podcast" to "study kit".**
   - NotebookLM bundles flashcards and quizzes with saved progress, slide decks, infographics and cinematic video [6].
   - Bar-Ilan University's Moodle agent produces summaries and quizzes in Hebrew, Arabic and English across 71 courses [49].
   - Turbo AI, StudyFetch and Coconote sell lecture → notes, flashcards and quizzes to students [51].

   M.B.S Studio's new HTML summary is the right direction. Quizzes and flashcards generated from the *same grounded plan* are the obvious next step.

5. **Open source is crowded but generic.**
   - Open Notebook (≈41.8k GitHub stars), SurfSense (≈16.8k) and Podcastfy (≈6.8k) are general "NotebookLM alternatives" [40][41].
   - None that I found targets Hebrew lectures, protected course video, or a grounding stage.
   - Local Hebrew TTS is now possible: Chatterbox Multilingual (MIT, ≈27.7k stars) lists Hebrew [40][56], and Phonikud provides open Hebrew G2P with real-time TTS [26].

   This makes a **fully offline Hebrew mode** a credible strategic bet that no competitor offers.

**One urgent, concrete finding from the codebase.** Several M.B.S Studio code paths still use or migrate to `eleven_turbo_v2_5` as a "Hebrew-compatible" model:

- `src/audio/elevenlabs_tts.py` (voice-override fallback);
- `src/utils/config.py` (automatic migration from `eleven_multilingual_v2`);
- `src/utils/quality_checker.py`.

ElevenLabs' current models page lists Turbo v2.5 as **deprecated** and says it supports 31 languages, **not including Hebrew** [14]. Hebrew is supported only on Eleven v3 and v4 [14]. I did not change any code; see recommendation Q1.

---

## 2. Landscape table

Legend: **C** = commercial, **OSS** = open source. "Grounding" means an explicit step that checks the generated script against the source, not just "uses the source as context".

| Product | Type | Inputs | Languages / Hebrew | Voices / TTS | Grounding / fact-check | Video | Local / privacy | Pricing | Notable strengths |
|---|---|---|---|---|---|---|---|---|---|
| **M.B.S Studio** | Local app (Python/FastAPI) | Local video/audio, web/course video via yt-dlp (cookies, Vimeo referer, impersonation), transcripts | Hebrew + English hosts; Hebrew ASR via ivrit.ai Whisper turbo | ElevenLabs Text-to-Dialogue (v3/v4), Gemini multi-speaker TTS, Azure; −16 LUFS | **Yes**: per-segment check against the transcript plus an edit pass that removes repetition | Slideshow of user or AI (Gemini) images, timed chapter titles, RTL, branding | Runs locally; keys in local `.env`; text and TTS go to the chosen cloud providers unless Ollama is used | Free software; user pays provider APIs (built-in cost meter) | Course-video acquisition, Hebrew ASR, editable plan, grounding, branding |
| **Google NotebookLM / Gemini Notebook** [1][2][6][7] | C | PDF, Docs/Slides/Sheets, txt/md/docx/pptx/epub, web URLs, **public YouTube with captions**, audio files, images [2] | 80+ languages; **Hebrew supported since Apr 2025** [8][9]; "Longer" length and Interactive mode English-only [1] | Google in-house voices (not user-selectable beyond format) | Chat answers cite sources; Audio Overviews "may contain inaccuracies" [1]; no user-visible per-claim check of the audio | Video Overviews (80 languages) [4]; **Cinematic** Video Overviews (English, 18+, paid tiers) [6] | Cloud only | Free tier; paid via Google AI Plus/Pro/Ultra and Workspace [3]; per-tier audio caps **(unverified, conflicting third-party data)** | Free, huge reach (Google cites 30M+ users, per a third-party report [7]), study kit (flashcards, quizzes, slides, infographics) [6] |
| **ElevenLabs GenFM / Studio podcasts** [16][17] | C | Text and URLs (API), docs in apps | Launched with 32 languages (2024) [17]; podcast API takes an ISO language code [16]; Hebrew depends on the model chosen (v3/v4 yes, v2 no) [14] | ElevenLabs voices; conversation or bulletin mode [16] | None documented [16] | No (audio) | Cloud | Paid subscription for the API; LLM cost "currently covered", audio billed [16]; TTS from $0.08/1k chars (API) [15] | Best-in-class voices, API, duration presets |
| **Wondercraft** [30] | C | Script, docs, prompts | ~28–30 languages per third parties; **Hebrew unverified** | Library voices, custom characters | None found | **Yes**: AI video, B-roll, avatars, up to 4K upscaling (Pro) [30] | Cloud | Free 150 credits; Creator $25/mo; Pro $45/mo; Enterprise custom [30] | Polished studio editor, team collaboration, video |
| **Jellypod** [31] | C | Docs, URLs, newsletters | "Multilingual hosts"; exact list not published [31]; **Hebrew unverified** | 3,300+ voices, up to 4 hosts, voice cloning [31] | None found; editable scripts [31] | Shorts, podcast video, audiograms [31] | Cloud | Seat-based; $20/seat/month or $240/year shown; credits 5k–30k per tier [31] | Publishing (RSS, websites, scheduled), automations |
| **ListenHub** [32] | C | Topic, URL, YouTube link, docs | 12 voice languages; podcast output reportedly limited to 3 languages **(third-party, unverified)** | ~900 voices, cloning | None found | Explainer videos, slide decks [32] | Cloud | ~$9–12/mo entry tier **(third-party, unverified)** | Fast "explain anything" podcasts, video and slides |
| **Adobe Acrobat Studio (PDF Spaces)** [34] | C | Up to 100 docs or links per PDF Space | Not documented in sources | Adobe (models not disclosed) | Not documented | Presentations (not video) | Cloud | Acrobat Studio subscription | Editable script; collaboration in PDF Spaces [34] |
| **Microsoft 365 Copilot audio overviews** [35] | C | Word, PDF, OneDrive files, meetings | English-only at launch **(current status unverified)** | Microsoft | Not documented | No | Cloud (M365 tenant) | M365 Copilot licence | Enterprise data boundary; podcast-style two-host option |
| **Speechify AI Podcasts** [33] | C | Docs, articles | "Multiple languages" (Feb 2026 PR); no list found | Speechify voices | None found | No | Cloud | Speechify subscription | Large TTS consumer base, publishing |
| **Huxe** [36] | C | Email, calendar, topics, news | Not verified | In-house | Not documented | No | Cloud | Not found | NotebookLM founders; personal daily audio; reported Apple licensing deal (single source, **unverified**) |
| **Recast** [37] | C | Article URLs | Not verified | Two hosts | None found | No | Cloud | Freemium (App Store) | Article → short dialogue |
| **Descript (Underlord)** [39] | C | Recorded media | Many (transcription); not a generator first | Overdub cloning, stock voices | N/A | Full video editor | Cloud | Credit-based tiers | Best editing UX; agentic co-editor |
| **Podcastle → Async** [38] | C | Recordings, text | Dubbing in 15+ languages **(third-party)** | Proprietary TTS | N/A | Limited | Cloud | ~$12–24/mo **(third-party, inconsistent)** | Recording and editing suite |
| **Open Notebook** (lfnovo) [40][41] | OSS, MIT | PDFs, video, audio, web | UI in 13+ languages; podcast language depends on provider | 1–4 speakers; many TTS providers (OpenAI, Google, ElevenLabs, Azure OpenAI, OpenAI-compatible) [41] | Chat cites sources; no podcast fact-check stage found | No | Self-hosted; "nothing leaves your machine" with local models [41] | Free | ≈41.8k stars, very active (push 2026-11-08) [40] |
| **SurfSense** [40][41] | OSS, Apache-2.0 | Docs plus many connectors | Not verified | Podcasts "voiced on your machine by default" [41] | Not documented | No | Air-gapped/offline capable [41] | Free core; some connectors need a licence [41] | ≈16.8k stars, very active [40] |
| **Podcastfy** [40][41] | OSS, Apache-2.0 | Web, PDF, images, YouTube, text, topic | Multilingual (examples FR, PT-BR) | OpenAI, Google, ElevenLabs, Edge TTS | No | No | Local LLM option [41] | Free | ≈6.8k stars; library/API; 2–5 min or 30+ min [41] |
| **NVIDIA PDF-to-Podcast Blueprint** [41] | OSS, Apache-2.0 | Target PDF plus context PDFs, guide prompt | Not documented | ElevenLabs | No explicit check [41] | No | Can run on a private network with NIM | Free (NIM licence for self-host) | ≈885 stars; agentic, enterprise reference |
| **Meta NotebookLlama** [41] | OSS (cookbook) | PDF | English-centric | Parler-TTS, Bark | No | No | Fully local (Llama 1B/8B/70B) | Free | Clear 4-step reference pipeline |
| **LlamaIndex notebookllama** [40] | OSS, MIT | Docs (LlamaCloud) | Not verified | Not verified | Not verified | No | Partly cloud (LlamaCloud) | Free | ≈2.0k stars |
| **Mozilla.ai document-to-podcast** [41] | OSS, Apache-2.0 | Docs | Not documented | Local | No | No | **No external API calls or GPU required** [41] | Free | ≈187 stars; minimal local reference |
| **Bar-Ilan Moodle AI agent** [49] | Institutional (Azure, Copilot Studio) | Course materials uploaded by staff | **Hebrew, Arabic, English** | N/A (text) | Answers from approved course materials | No | University tenant | Free to students | Quizzes with explanations; 71 courses, ~2,400 students |
| **Soniox App** [50] | C | Live or recorded speech | Hebrew transcription and summaries (vendor claims) | N/A | N/A | No | Says recordings are not stored or used for training [50] | Free credits weekly plus paid | Hebrew lecture capture on mobile and desktop |
| **Turbo AI / StudyFetch / Coconote** [51] | C | Recordings, PDFs | Not verified for Hebrew | Turbo AI lists podcasts | Not documented | No | Cloud | ~$5–20/mo **(third-party, unverified)** | Notes → flashcards → quizzes loop |

---

## 3. Deep-dive comparison

### 3.1 Hebrew quality (ASR, script, TTS)

**ASR.**
- M.B.S Studio uses ivrit.ai's `whisper-large-v3-turbo-ct2` (Apache-2.0, trained on `crowd-transcribe-v5`) [23][24].
- A third-party harness that reproduces the ivrit.ai leaderboard lists the ivrit.ai turbo model at:
  - **5.5% WER** on `eval-d1`;
  - **6.1%** on `eval-whatsapp`;
  - **8.1%** on a KAN broadcast set.

  That compares with 29.9–39.6% for vanilla `whisper-small` [25]. These are not official leaderboard figures, and I could not load ivrit.ai's leaderboard Space itself.
- ivrit.ai is very active. In the past few weeks it has published a streaming Hebrew model (`zipformer2-66m-streaming-he`), an ONNX turbo build, a Hebrew forced-alignment evaluation, and the VoxKnesset dataset [23][40].
- NotebookLM accepts audio files and *captioned public* YouTube videos [2]. It does not publish Hebrew ASR accuracy.
- The CHI EA'25 diary study of AI podcasts at the University of St. Gallen flags "technical non-English language issues" [48] *(search snippet)*.

**Script.**
- Hebrew-native writing quality is not benchmarked anywhere I could find.
- The Jerusalem Post's May 2025 review of NotebookLM in Hebrew calls it "not perfect", citing "occasional translation errors or mispronunciations" [9].
- M.B.S Studio's English-only quality harness (10 scenarios) does not yet measure Hebrew script quality.

**TTS.** The best public evidence is ivrit.ai's blind **TTS Arena Hebrew** [22]:

| Rank | Model | ELO | Votes |
|---|---|---|---|
| 1 | Gemini 3.1 Flash TTS | 1585 | 202 |
| 2 | Deepdub dd-etts-3.3 | 1578 | 203 |
| 3 | ElevenLabs v3 | 1546 | 207 |
| 4 | Soniox v1 | 1539 | 167 |
| 7 | Inworld TTS-2 | 1490 | 144 |
| 10–12 | OpenAI GPT-4o-mini-TTS / TTS-1 HD / TTS-1 | 1451 / 1442 / 1417 | ~170–197 |

Caveats:
- Roughly 200 votes per model is a small sample. The top three are within about 40 ELO.
- The arena measures single-utterance quality, not two-host dialogue.
- **Azure and ElevenLabs v4 do not appear** in the arena, so their relative Hebrew quality is unverified.
- Deepdub (an Israeli company) markets itself as #1 on this arena [29]. The live board showed it at #2 on the access date.

Provider facts relevant to M.B.S Studio:
- **Gemini TTS.** The current docs list `gemini-3.8-flash-tts` and `gemini-3.8-flash-lite-tts` with Hebrew ✔ and up to **2 speakers** per multi-speaker request [18]. `gemini-2.5-flash-preview-tts`, `gemini-2.5-pro-preview-tts` and `gemini-3.1-flash-tts-preview` shut down on **2026-11-17** [19]. M.B.S Studio already references `gemini-3.8-flash-tts`, which is good. Make sure no fallback still points at a retiring ID.
- **ElevenLabs.**
  - Hebrew is listed for Eleven v3 and v4 only. Multilingual v2, Flash v2.5 and Turbo v2.5 do not include Hebrew [14].
  - Text-to-Dialogue is limited to **2,000 characters of text per request** and has no speaker limit [13].
- **Azure.** Hebrew neural voices (`he-IL-HilaNeural`, `he-IL-AvriNeural`) have existed since 2020 [28] *(search snippet)*. I found no evidence of Hebrew in Azure's multi-talker or Dragon HD sets **(unverified)**.
- **Open source.**
  - Chatterbox Multilingual lists Hebrew and ships a Hebrew diacritics helper. v3 (June 2026) covers 25 languages [56] *(search snippet)*.
  - Phonikud (arXiv 2506.12311) is open Hebrew G2P. The authors report that small local TTS models using it "approach" proprietary systems [26] *(search snippet)*.
  - HebTTS (diacritic-free Hebrew LM-TTS) has ≈113 stars [40].

**Verdict.** M.B.S Studio's Hebrew ASR is likely *better* than generic tools; the evidence is indirect but strong. Its TTS is *on par* with the best, because it uses the same vendors. Its Hebrew script quality is *unmeasured*. That last point is both the main risk and the main opportunity.

### 3.2 Faithfulness and grounding to the source lecture

| | Approach | Evidence |
|---|---|---|
| NotebookLM | Source-grounded generation; citations in chat; "explore quotes/citations" while listening [1] | AGU study: errors in **all 3** overviews, typically near the end, including unjustified extrapolation [11]. Michigan LSA consultants saw more errors with web sources that have gaps [12] *(search snippet)* |
| ElevenLabs podcast API | None documented [16] | — |
| NVIDIA Blueprint | Target and context PDFs; no explicit check [41] | — |
| Podcastfy / Open Notebook / SurfSense | Context-grounded; no podcast fact-check stage found [41] | — |
| **Research frontier** | Turn-level LLM-as-judge plus **catch-n-repair** rewrite [42]; PodBench scores hallucination-free scripts [43] *(search snippet)*; PodEval covers audio-level evaluation [44] *(search snippet)* | GPT-4o "frequently" ungrounded in 1,500+ documents across 5 domains [42] |
| **M.B.S Studio** | Per-segment grounding against the transcript plus a whole-episode edit pass that adds no new information | Not yet measured or exposed to users |

**Verdict.** This is M.B.S Studio's most defensible technical edge. Its design matches the best current research [42]. However:

1. Users cannot *see* it. There are no per-claim timestamps or citations back to the lecture.
2. It has no metric (for example, % of unsupported turns before and after repair).
3. A lecture transcript contains ASR errors. Grounding against a 5–8% WER transcript can "validate" a misheard term, so grounding should be ASR-error-aware.

### 3.3 Pipeline control (editable plan)

- NotebookLM gives a format, a focus prompt and a length setting, but **no editable outline or script** before audio [1].
- Adobe offers a "fully editable script" for shared PDF Spaces [34] *(search snippet)*.
- Jellypod advertises editable scripts [31].
- Open Notebook offers "full script control" with episode profiles [41].
- ElevenLabs has intro/outro and an instructions prompt [16].

M.B.S Studio's **analysis → editable episode plan → segment writing** design gives the most pedagogical control of anything reviewed. It is especially strong for lecturers who want to set the emphasis. Its weaknesses:

- No collaborative review, for example a lecturer approving a TA's plan.
- No plan templates that can be reused across a course series.

### 3.4 Privacy and local-first

**What M.B.S Studio keeps local:**
- recordings, video files, transcripts (Whisper runs locally);
- keys (local `.env`);
- the orchestration itself.

**What leaves the machine:** script writing (Gemini or Azure OpenAI) and TTS (ElevenLabs, Gemini or Azure) send *text* to cloud providers. Only checks can run on a local Ollama model (gemma3).

**How others compare:**
- **SurfSense** (air-gapped option, local voices by default [41]) and **Mozilla's blueprint** (no external API calls [41]) are more local than M.B.S Studio today, but they lack Hebrew-quality components.
- **NotebookLM** is cloud-only. It disables public sharing for Workspace Enterprise and Education accounts [1].

**Verdict.** M.B.S Studio is "local-first with cloud intelligence". That is honest and useful, but marketing it as "private" without qualification would overclaim. A truly offline Hebrew mode (local ASR + local LLM + Chatterbox or Phonikud TTS) would be unique in the category.

### 3.5 Cost

The following are my own **estimates**, from list prices on the access date, for one 15-minute Hebrew two-host episode (≈12k characters of script, an assumption):

| TTS route | Unit price | Est. TTS cost per episode |
|---|---|---|
| ElevenLabs v3/v4 API | $0.08 per 1k chars [15] | ≈ $0.96 |
| Gemini 3.8 Flash TTS (preview) | $9 per 1M audio tokens through 2026-12-31, then $18; 25 tokens/s [21] *(search snippet)* | ≈ $0.20 (≈ $0.41 from 2027) |
| Gemini 3.8 Flash-Lite TTS | $6 per 1M audio tokens, then $12 [21] *(search snippet)* | ≈ $0.14 (≈ $0.27 from 2027) |

LLM writing, grounding and image costs come on top and depend on the models chosen.

For comparison:
- **NotebookLM** is free at the entry tier, with daily caps **(caps unverified)** [3].
- **Wondercraft** costs $25–45/month [30].
- **Jellypod** costs $20/seat/month [31].

M.B.S Studio's **pay-per-use** model with a live **cost meter** is cheaper than SaaS for light users. For a student it is costlier than free NotebookLM. The cost meter is a real differentiator for institutions that need budget predictability.

### 3.6 Video output

- **NotebookLM** has narrated-slide Video Overviews in 80 languages [4], and **Cinematic** Video Overviews (Gemini + Nano Banana Pro + Veo, English-only, paid tiers, 18+) [6].
- **Wondercraft** offers AI video and avatars up to 4K [30].
- **Jellypod** offers Shorts and audiograms [31].
- **ListenHub** offers explainer videos [32].

M.B.S Studio offers a branded, **RTL-correct** slideshow with chapter titles timed to the audio. For Hebrew that is a practical advantage: RTL text rendering in generated video is a common failure point, though I found no systematic study of competitors' Hebrew video. M.B.S Studio lags on motion, lecture-slide reuse, and vertical clips for social or course portals.

### 3.7 Learning outcomes (summaries, quizzes, flashcards)

- **Evidence base.**
  - PAIGE (180 US students): *personalised* AI podcasts gave significantly better learning outcomes than textbook reading, but the effect was subject-specific [46].
  - LLM-guided reflection prompts inside AI podcasts did not improve outcomes and reduced perceived attractiveness (36 undergraduates) [47].
  - The AGU study warns that errors are hard for non-experts to detect [11].

  Takeaway: podcasts are engaging, and gains depend on personalisation and accuracy, not on added interactivity.
- **Competitors.**
  - NotebookLM: flashcards and quizzes with saved progress, "Got it / Missed it", shuffle, and rerun-missed [6].
  - Bar-Ilan's agent: Hebrew quizzes with explanations; reported study time fell from 5–6 h to ~3 h for one student, and one course scored +3 points on finals (the lecturer notes cohort effects) [49].
  - Turbo AI, StudyFetch and Coconote: the full notes → flashcards → quiz loop [51].
- **M.B.S Studio.** The *exam prep* format plus the upcoming HTML lesson summary are a good start. There are no quizzes, flashcards or spaced repetition yet, and no learning-outcome measurement.

---

## 4. Where M.B.S Studio is genuinely differentiated, and where it lags

### Genuinely differentiated

1. **Input acquisition from gated course platforms.** yt-dlp with cookies.txt, embed-only Vimeo via Referer, and browser impersonation. NotebookLM only accepts *public, captioned* YouTube videos [2]. No reviewed competitor offers authenticated course-video ingestion. *Caveat:* users must respect their institution's terms and copyright. Build this into the UX (see S6).
2. **Hebrew-specialist ASR in the loop** (ivrit.ai turbo, ≈5.5–6.1% WER on ivrit.ai eval sets per a third-party reproduction [25]), saved next to the video.
3. **Grounding stage plus a non-additive edit pass.** This aligns with state-of-the-art faithfulness research [42]. No commercial competitor documents an equivalent.
4. **Editable episode plan before voicing.** NotebookLM has none [1].
5. **Multi-vendor TTS with a cost meter and loudness normalisation.** The user can pick the Hebrew arena leader of the month [22].
6. **RTL-correct, branded video and summary**, suited to Israeli institutions and white-labelling.
7. **Local orchestration**: keys and media stay on the machine; Windows, macOS and Docker builds.
8. **An evaluation harness with regression comparison**, which is rare outside research code.

### Where it lags

1. **No study loop** (quizzes, flashcards, spaced repetition) [6][49][51].
2. **Grounding is invisible and unmeasured.** There are no timestamped citations, faithfulness score or published benchmark.
3. **The evaluation harness is English-only.** Hebrew script quality is untested, although Hebrew is the core market.
4. **Not fully offline.** LLM writing and TTS are cloud-dependent; competitors SurfSense and Mozilla's blueprint advertise offline modes [41].
5. **Distribution.** No RSS, LMS (Moodle) export or sharing link. Jellypod has publishing [31]; NotebookLM has share links [1].
6. **Interactivity.** NotebookLM's Interactive mode lets learners "join" the hosts, English only [1]. Research suggests interactivity is not a learning win by itself [47], so this is low priority.
7. **Video richness.** No lecture-slide extraction, motion or vertical clips [6][30][31].
8. **Scale and onboarding.** It is a local install that needs several API keys, against zero-setup SaaS.
9. **Stale model IDs in code paths** (`eleven_turbo_v2_5` described as Hebrew-capable). Contradicted by ElevenLabs' docs [14].

---

## 5. Prioritised recommendations

### Quick wins (days to ~2 weeks)

| # | Recommendation | Rationale | Evidence |
|---|---|---|---|
| **Q1** | **Remove `eleven_turbo_v2_5` as a Hebrew default.** Stop migrating `eleven_multilingual_v2` → `eleven_turbo_v2_5`; default Hebrew single-voice and preview paths to `eleven_v3` (or `eleven_v4` / `eleven_v4_turbo`). Add a startup check that rejects Hebrew + non-Hebrew models. | Turbo v2.5 is deprecated and does not list Hebrew. Silent quality loss or mispronunciation is likely on override, preview and quality-checker paths. | [14] |
| **Q2** | **Model-ID guardrail.** Add a test that fails if any retiring Gemini TTS ID (`gemini-2.5-*-preview-tts`, `gemini-3.1-flash-tts-preview`) appears in code or settings, and a settings migration for users with saved old IDs. | Shutdown on 2026-11-17, one week after this report. | [19] |
| **Q3** | **Show the grounding.** For every script segment, show the supporting transcript timestamp(s) and a ✔/⚠ badge. Export them as citations in the HTML summary. | Turns the invisible differentiator into a visible trust feature. NotebookLM's main criticism is undetectable errors. | [11][42] |
| **Q4** | **Quizzes and flashcards from the grounded plan.** Generate MCQs and open questions with answer explanations and transcript timestamps. Export to Anki/CSV and Moodle XML or GIFT. | Closes the main parity gap with NotebookLM and Bar-Ilan's agent. Reuses the existing plan and grounding data. | [6][49] |
| **Q5** | **Hebrew scenarios in the eval harness.** Add 5+ Hebrew lecture scenarios with a Hebrew-literate critic. Track faithfulness (% unsupported turns), repetition and terminology preservation. | The core market is untested. The research shows faithfulness can be measured turn by turn. | [42][43] |
| **Q6** | **Respect Text-to-Dialogue limits.** Make sure dialogue chunks stay ≤2,000 characters per request, with tests. | Documented hard limit. | [13] |
| **Q7** | **Make the TTS picker data-driven.** Show "Hebrew quality rank (ivrit.ai arena, date)" next to each voice provider, and offer Gemini 3.8 Flash TTS as the low-cost default. | Gemini Flash TTS leads the arena and is ~4–5× cheaper per episode by my estimate. Users choose better with evidence. | [15][21][22] |

### Strategic bets (1–6 months)

| # | Recommendation | Rationale | Evidence |
|---|---|---|---|
| **S1** | **Publish a "Faithful Hebrew Lecture Podcast" benchmark.** Use 20–30 consented lectures (or the Knesset and ivrit.ai datasets). Report unsupported-turn rate before and after grounding for M.B.S Studio vs. NotebookLM on the same transcripts. | Turns the main technical edge into a credible, citable claim. The academic framing already exists. | [11][42][23] |
| **S2** | **ASR-aware grounding.** Pass word-level confidences and timestamps from faster-whisper into the grounding step. Flag low-confidence terms for user review, with a course glossary or custom vocabulary for names and jargon. | A 5–8% WER transcript can "ground" a misrecognised term. ivrit.ai is investing in forced alignment and evaluation. | [23][25] |
| **S3** | **Offline Hebrew mode.** Local LLM (Ollama) for writing and checking, plus a local Hebrew TTS (Chatterbox Multilingual v3 with Hebrew diacritics, or Phonikud-based TTS). Blind-test it against cloud voices with an arena-style A/B. | Would be the only fully offline Hebrew podcast generator. Strong for universities and privacy-sensitive users. Open models now list Hebrew. | [26][56][41] |
| **S4** | **Course mode.** Multi-lecture series with a shared glossary, cross-lecture "previously on…" recaps, plan templates, and a final exam-prep episode with a question bank. Ties into the chained download → transcribe → podcast flow now in development. | Competitors are notebook-centric, not course-centric. Lecturers think in semesters. | [6][49] |
| **S5** | **Learning-outcome pilot.** Partner with one Israeli course to run a small A/B (podcast + quiz vs. summary only) and measure quiz scores and time-on-task. | The evidence base is thin and mixed. Original Hebrew-learner data would be a moat and a sales asset. | [46][47][49] |
| **S6** | **Institutional / LMS edition.** Moodle export (SCORM or H5P/GIFT quizzes, MP4, HTML summary), lecturer approval of the plan, and an explicit consent and copyright notice for course-video downloads. | Bar-Ilan shows institutional appetite and that Moodle is the delivery channel. It also reduces legal risk from authenticated downloading. | [49] |
| **S7** | **Richer video for lectures.** Extract the lecturer's own slides and board frames from the source video (scene detection), plus vertical 60-second recap clips. | Reuses real lecture visuals (more faithful than AI images). Competes with Video Overviews and Jellypod Shorts. | [4][6][31] |
| **S8** | **Watch the Deepdub and Soniox Hebrew voices** as optional providers once their APIs and pricing suit individuals. | Both are near the top of the Hebrew arena. Deepdub's public pricing is enterprise-scale ($2,000/month tier on AWS Marketplace, per a search snippet). | [22][29] |

**Suggested order:** Q1 → Q2 → Q6 (correctness, this week) → Q3 + Q5 (make the differentiator visible and measured) → Q4 (study loop) → S2 → S1 → S4/S6 → S3 → S5/S7.

---

## 6. Things I could not verify

- Whether NotebookLM's *Hebrew* Audio Overviews now support the "Longer" length. The help page says Longer is English-only [1].
- Current NotebookLM daily Audio/Video Overview caps per plan. Third-party sources conflict, and Google's page lists notebook and source limits only [3].
- Hebrew support in Wondercraft, Jellypod, ListenHub, Speechify, Adobe, Turbo AI, StudyFetch and Coconote.
- Azure Hebrew support in multi-talker or Dragon HD voices.
- Hebrew quality of ElevenLabs **v4** (not on the arena at access time).
- ivrit.ai's official leaderboard WER for the CT2 turbo build (the Space would not load).
- Google Illuminate's current status (no 2026 primary source found).
- The reported Apple–Huxe licensing deal (single secondary source).
- Exact current prices for ListenHub, Podcastle/Async, Turbo AI, StudyFetch and Coconote (third-party data only, inconsistent).

---

## Sources

All accessed **2026-11-10**. *(search snippet)* = seen in search results only, not opened in full.

1. Google NotebookLM Help, "Generate Audio Overview in NotebookLM": https://support.google.com/notebooklm/answer/16212820?hl=en
2. Google NotebookLM Help, add sources and source limits: https://support.google.com/notebooklm/answer/16215270?hl=en
3. Google NotebookLM Help, "Upgrade NotebookLM" (plan limits): https://support.google.com/notebooklm/answer/16213268?hl=en
4. Google Blog, "NotebookLM's Video Overviews are now available in 80 languages" (2025-08-25): https://blog.google/innovation-and-ai/models-and-research/google-labs/notebook-lm-audio-video-overviews-more-languages-longer-content/
5. Google Blog, "What's new in NotebookLM: Video Overviews and an upgraded Studio" (2025-07-29) *(search snippet)*: https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-video-overviews-studio-upgrades/
6. Google Workspace Updates, "New ways to customize and interact with your content in NotebookLM" (2026-03-20): https://workspaceupdates.googleblog.com/2026/03/new-ways-to-customize-and-interact-with-your-content-in-NotebookLM.html
7. Digital Trends, "NotebookLM just got a new name…" (2026-07-16): https://www.digitaltrends.com/computing/notebooklm-just-got-a-new-name-and-a-serious-upgrade-for-google-ai-pro-subscribers/ ; also PPC Land (30M users) *(search snippet)*: https://ppc.land/google-kills-notebooklm-name-moving-30-million-users-to-gemini-notebook/
8. TechCrunch, "Google's NotebookLM expands its AI podcast feature to more languages" (2025-04-29) *(search snippet)*: https://techcrunch.com/2025/04/29/googles-notebooklm-expands-its-ai-podcast-feature-to-more-languages
9. Jerusalem Post, NotebookLM now available in Hebrew (2025-05-17): https://www.jpost.com/consumerism/article-852808
10. TechCrunch, NotebookLM Brief/Critique/Debate formats (2025-09-03) *(search snippet)*: https://techcrunch.com/2025/09/03/googles-notebooklm-now-lets-you-customize-the-tone-of-its-ai-podcasts
11. Pittwire (Univ. of Pittsburgh), Ian Flynn on NotebookLM podcasts, AGU *Perspectives of Earth and Space Scientists* (2026-02-17): https://www.pittwire.pitt.edu/features-articles/2026/02/17/ian-flynn-notebooklm-podcasts-agu-perspectives
12. Univ. of Michigan LSA, "A Critical Look at NotebookLM" (2026-03-12) *(search snippet)*: https://sites.lsa.umich.edu/learningteachingconsulting/2026/03/12/a-critical-look-at-notebooklm/
13. ElevenLabs Docs, Text to Dialogue: https://elevenlabs.io/docs/overview/capabilities/text-to-dialogue
14. ElevenLabs Docs, Models (language lists, deprecated models): https://elevenlabs.io/docs/overview/models
15. ElevenLabs API pricing: https://elevenlabs.io/pricing/api
16. ElevenLabs API reference, Studio "Create podcast": https://elevenlabs.io/docs/api-reference/studio/create-podcast
17. TechCrunch, ElevenLabs GenFM launch (2024-11-27) *(search snippet)*: https://techcrunch.com/2024/11/27/elevenlabs-new-feature-is-a-notebooklm-competitor-for-creating-genai-podcasts
18. Google AI Studio docs, Speech generation (TTS models, language table, 2-speaker limit): https://aistudio.google.com/docs/speech-generation
19. Google AI Studio docs, Deprecations: https://aistudio.google.com/docs/deprecations
20. Google AI Studio docs, Models (image model IDs): https://aistudio.google.com/docs/models
21. Google Cloud Text-to-Speech pricing (Gemini 3.8 Flash/Flash-Lite TTS) *(search snippet)*: https://cloud.google.com/text-to-speech/pricing ; secondary: https://www.usagepricing.com/blueprint/activity/google-gemini-2026-10-06-flash-tts-launch *(search snippet)*
22. ivrit.ai, TTS Arena Hebrew leaderboard: https://ivrit-ai-tts-arena-hebrew.hf.space/leaderboard (Space: https://huggingface.co/spaces/ivrit-ai/TTS-Arena-Hebrew)
23. ivrit.ai on Hugging Face (models, datasets, leaderboards): https://huggingface.co/ivrit-ai
24. ivrit.ai `whisper-large-v3-turbo-ct2` model card *(search snippet)*: https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ct2
25. Ozen-v1 model card (reproduced ivrit.ai Hebrew WER table): https://huggingface.co/itayinbar/Ozen-v1
26. Phonikud, Hebrew G2P for real-time TTS, arXiv 2506.12311 *(search snippet)*: https://arxiv.org/abs/2506.12311 ; https://phonikud.github.io/
27. HebTTS (slp-rl), GitHub API metadata: https://api.github.com/repos/slp-rl/HebTTS
28. Microsoft Tech Community, Azure Neural TTS 51 new voices (Avri, Hila) *(search snippet)*: https://techcommunity.microsoft.com/t5/azure-ai/azure-neural-text-to-speech-updates-51-new-voices-added-to-the/ba-p/1988418
29. Deepdub Hebrew eTTS benchmark page *(search snippet)*: https://deepdub.ai/model-benchmark/etts-benchmark-hebrew ; AWS Marketplace listing *(search snippet)*: https://aws.amazon.com/marketplace/pp/prodview-ymif4bkmmetk2
30. Wondercraft pricing: https://www.wondercraft.ai/pricing
31. Jellypod pricing (llms.txt): https://www.jellypod.com/pricing/llms.txt
32. ListenHub, App Store listing *(search snippet)*: https://apps.apple.com/app/listenhub/id6743071290 ; Toolmage listing *(search snippet)*: https://www.toolmage.com/en/tool/listenhub/
33. Speechify, "Speechify launches AI podcast publishing" (2026-02-16) *(search snippet)*: https://speechify.com/news/speechify-launches-ai-podcast-publishing/
34. Adobe News, Acrobat Studio update (2026-01) *(search snippet)*: https://news.adobe.com/news/2026/01/adobe-acrobat-studio-transforms ; Engadget *(search snippet)*: https://www.engadget.com/ai/adobe-acrobat-can-now-generate-presentations-and-audio-podcasts-from-your-documents-140000146.html
35. Microsoft 365 Insider, audio overview of a document *(search snippet)*: https://techcommunity.microsoft.com/t5/microsoft-365-insider-blog/listen-to-an-audio-overview-of-a-document-with-microsoft-365/ba-p/4439362
36. TechCrunch, Huxe launch (2025-09-23) *(search snippet)*: https://techcrunch.com/2025/09/23/former-notebooklm-devs-new-app-huxe-taps-audio-to-help-you-with-news-and-research ; iClarified, Apple–Huxe deal (2026-10-11) *(search snippet)*: https://www.iclarified.com/102723/apple-strikes-ai-audio-deal-with-huxe-founded-by-former-notebooklm-developers
37. Recast, App Store listing *(search snippet)*: https://apps.apple.com/app/apple-store/id1660569513
38. Podcastle/Async pricing (Costbench, third-party) *(search snippet)*: https://www.costbench.com/software/ai-voice-tools/podcastle/
39. Descript podcasting page *(search snippet)*: https://www.descript.com/podcasting
40. GitHub REST API repository metadata (stars, last push, licence), queried 2026-11-10:
    - https://api.github.com/repos/lfnovo/open-notebook (41,806★, pushed 2026-11-08)
    - https://api.github.com/repos/MODSetter/SurfSense (16,824★, pushed 2026-11-10)
    - https://api.github.com/repos/souzatharsis/podcastfy (6,799★, pushed 2026-05-04)
    - https://api.github.com/repos/gabrielchua/open-notebooklm (2,590★, pushed 2024-12-07)
    - https://api.github.com/repos/run-llama/notebookllama (1,980★)
    - https://api.github.com/repos/NVIDIA-AI-Blueprints/pdf-to-podcast (885★, pushed 2026-10-14)
    - https://api.github.com/repos/mozilla-ai/document-to-podcast (187★)
    - https://api.github.com/repos/lfnovo/podcast-creator (130★)
    - https://api.github.com/repos/meta-llama/llama-cookbook (18,588★; hosts NotebookLlama)
    - https://api.github.com/repos/resemble-ai/chatterbox (27,722★)
    - https://api.github.com/repos/microsoft/VibeVoice (56,921★)
    - https://api.github.com/repos/nari-labs/dia (19,408★)
    - https://api.github.com/repos/fishaudio/fish-speech (33,649★)
    - https://api.github.com/repos/hexgrad/kokoro (10,025★)
    - https://api.github.com/repos/boson-ai/higgs-audio (8,382★)
    - https://api.github.com/repos/OpenMOSS/MOSS-TTSD (1,419★)
    - https://api.github.com/repos/SYSTRAN/faster-whisper (26,634★)
    - https://api.github.com/repos/yt-dlp/yt-dlp (206,550★)
    - https://api.github.com/orgs/ivrit-ai/repos (ivrit.ai org repos)
41. GitHub READMEs:
    - https://github.com/lfnovo/open-notebook
    - https://github.com/souzatharsis/podcastfy
    - https://github.com/MODSetter/SurfSense
    - https://github.com/NVIDIA-AI-Blueprints/pdf-to-podcast
    - https://github.com/mozilla-ai/document-to-podcast
    - https://github.com/meta-llama/llama-cookbook/tree/main/end-to-end-use-cases/NotebookLlama
42. Dutta, Dhamecha, Shivaswamy, "On Improving Faithfulness of Podcasts from Documents", arXiv 2607.21961 (2026-07-24): https://arxiv.org/abs/2607.21961
43. PodBench, arXiv 2601.14903 *(search snippet)*: https://arxiv.org/html/2601.14903
44. PodEval, arXiv 2510.00485 *(search snippet)*: https://arxiv.org/html/2510.00485v1
45. PodAgent, arXiv 2503.00455 *(search snippet)*: https://arxiv.org/abs/2503.00455
46. PAIGE: personalised AI-generated educational podcasts, arXiv 2409.04645: https://arxiv.org/abs/2409.04645
47. LLM-guided reflection in interactive AI podcasts, arXiv 2508.04787: https://arxiv.org/abs/2508.04787
48. Univ. of St. Gallen, CHI EA'25 diary study of AI-generated podcasts *(search snippet)*: https://alexandria.unisg.ch/entities/publication/4a4f1434-8618-46d2-9595-dead85338b8b
49. Microsoft Source EMEA, "Missed a lecture? This AI agent at Bar-Ilan University helps students catch up" (2026-10-15): https://news.microsoft.com/source/emea/features/missed-a-lecture-this-ai-agent-at-bar-ilan-university-helps-students-catch-up/
50. Soniox App, lecture summaries in Hebrew: https://soniox.com/soniox-app/for/university-students-lecture-summaries-in-hebrew
51. tl;dv, StudyFetch review 2026 *(search snippet)*: https://tldv.io/blog/studyfetch-review/ ; Toolradar, best AI study tools *(search snippet)*: https://toolradar.com/guides/best-ai-study-tools
52. Startupim, Quizit.ai (Israeli quiz/flashcard startup) *(search snippet)*: https://startupim.com/company/quizit-ai
53. NotebookLM third-party pricing/limits (conflicting; not relied on) *(search snippet)*: https://elephas.app/blog/notebooklm-daily-limit
54. 9to5Google, NotebookLM Video Overviews launch (2025-07-29) *(search snippet)*: https://9to5google.com/2025/07/29/notebooklm-video-overviews/
55. Google Developer Forum, "Hebrew text to speech using Gemini" (older; superseded by [18]) *(search snippet)*: https://discuss.google.dev/t/hebrew-text-to-speech-using-gemini/192380
56. Resemble AI, Chatterbox Multilingual v3 (25 languages incl. Hebrew, June 2026) *(search snippet)*: https://www.resemble.ai/resources/chatterbox-multilingual-v3-tts-with-embedded-watermarking-for-25-languages ; https://github.com/resemble-ai/chatterbox *(search snippet)*

*Codebase observations (Q1, Q2) come from read-only inspection of `src/audio/elevenlabs_tts.py`, `src/utils/config.py`, `src/utils/quality_checker.py`, `src/video/ai_images.py` and `src/utils/pricing.py` on 2026-11-10. No project files other than this report were modified.*
