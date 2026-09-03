make_data.py — hand-labeled synthetic data for the TextGrad prompt-optimization demo.

Task (a CFPS-style digital-divide coding example): label a short description of an
internet activity as its *type of use* — CAPITAL vs LEISURE — under a DELIBERATELY
NARROW codebook:

    CAPITAL = the activity COMPLETES A TRANSACTION or OFFICIAL / ECONOMIC ACTION:
              applying, submitting, filing, paying, transferring, registering,
              booking an official appointment, signing, sending work communication.
    LEISURE = everything else — including *information-seeking and self-directed
              learning* (reading news, looking things up, watching a tutorial or
              documentary, practicing on an app) as well as pure entertainment.

Why the narrow rule.  A modern 7B model already nails the *intuitive* "productive vs
recreational" split (~100% from a vague prompt) — leaving nothing to optimize. This
codebook deliberately diverges from intuition on the "info-seeking / learning" items
(ids 21-30): a vague prompt calls them CAPITAL (they look productive), but the codebook
says LEISURE. That gap is what TextGrad must discover from labeled examples and encode
into the prompt. (The rule mirrors a real distinction in the digital-inequality
literature between *transactional/production* uses and *information/consumption* uses;
here it is sharpened to make the demo measurable.)

Run:  python make_data.py   ->   synthetic_data.csv  (+ CODEBOOK.md)

## Counterintuitive items (a vague prompt is expected to mislabel these as CAPITAL)

- `21` gold=**LEISURE** — Reading the morning news about the national economy.
- `22` gold=**LEISURE** — Looking up the symptoms of high blood pressure.
- `23` gold=**LEISURE** — Watching a YouTube tutorial about Excel formulas.
- `24` gold=**LEISURE** — Searching for scholarship and admission information.
- `25` gold=**LEISURE** — Watching a documentary explainer on climate policy.
- `26` gold=**LEISURE** — Practicing English on a language-learning app.
- `27` gold=**LEISURE** — Researching farming techniques to raise crop yield.
- `28` gold=**LEISURE** — Reading product reviews before deciding on a laptop.
- `29` gold=**LEISURE** — Looking up legal information about a rental contract.
- `30` gold=**LEISURE** — Checking the weather forecast for the week.
