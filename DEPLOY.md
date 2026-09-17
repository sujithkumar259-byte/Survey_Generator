# Deploy runbook (hackathon / public)

Two services, deployed separately:

1. **Survey Generator** (Python · Streamlit) — Phases 1 & 2 (docs → hypotheses → survey-outline `.xlsx`)
2. **Stage-3 Dashboard** (R · Shiny) — Phase 3 (survey-outline `.xlsx` → formatted Word `.docx`)

Each runs independently; an analyst can also enter at any phase (raw docs → P1, a hypotheses list → P2, a finished workbook → P3).

---

## A. Streamlit app → Streamlit Community Cloud (free, public)

**Repo layout (already prepared):**
```
<repo root>/
├── survey_generator/      # the Python package (app.py + phase1/ phase2/ llm/ ...)
├── requirements.txt       # at root — Streamlit Cloud reads this
└── .gitignore
```
`survey_generator/app.py` adds its parent to `sys.path`, so the absolute
`survey_generator.*` imports resolve when the main file is `survey_generator/app.py`.

**Steps**
1. Push this folder to a new GitHub repo:
   ```bash
   git remote add origin https://github.com/<you>/<repo>.git
   git branch -M main
   git push -u origin main
   ```
2. Go to https://share.streamlit.io → **New app** → pick the repo + `main` branch.
3. Set **Main file path** = `survey_generator/app.py`.
4. (Optional) **Advanced → Python version** 3.11.
5. Deploy. You get a public `https://<app>.streamlit.app` URL.

**API key:** leave it as the in-app sidebar input so each user brings their own key.
Do **not** bake a key into a public app. (If you must preset one for a demo, put it in
Streamlit **Secrets**, not in the repo — but anyone using the public app would spend it.)

---

## B. R Shiny dashboard → shinyapps.io (free tier, public)

Requires R installed locally (the deploy is driven from R).

1. Create a free account at https://www.shinyapps.io and copy your token/secret
   (Account → Tokens).
2. In R / RStudio:
   ```r
   install.packages("rsconnect")
   rsconnect::setAccountInfo(name="<acct>", token="<token>", secret="<secret>")
   rsconnect::deployApp("stage3_shiny_app_compact_v3_3")
   ```
   `rsconnect` auto-detects the packages (shiny, bslib, DT, readxl, officer, flextable).
3. You get a public `https://<acct>.shinyapps.io/<app>` URL.

---

## C. (Later) Merge into one UI
Once the Shiny URL exists, embed it as a "Phase 3" tab inside the Streamlit app via
`st.components.v1.iframe(SHINY_URL)`. Keeps the R dashboard intact; users see one app.
(Confirm the Shiny host allows iframing — `X-Frame-Options`. shinyapps.io generally does.)

---

## Notes
- `survey_generator.bak/` (the pre-phase3-removal backup) is git-ignored — it won't deploy.
- Tests: from the repo root, `pip install pytest && python -m pytest survey_generator/tests -q` (35 passing).
