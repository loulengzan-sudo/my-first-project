"""
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
"""
import csv
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))

# (id, text, gold_label, counterintuitive?)  counterintuitive = a vague/intuitive
# prompt is expected to get it WRONG (these are the info-seeking / learning items).
ROWS = [
    # ---- CAPITAL: transactional / official / economic ACTIONS (20) ----
    (1,  "Applying for a job on a recruitment website.", "CAPITAL", False),
    (2,  "Sending a work email to a client about a deadline.", "CAPITAL", False),
    (3,  "Filing an income tax return on the government portal.", "CAPITAL", False),
    (4,  "Transferring money to a supplier via online banking.", "CAPITAL", False),
    (5,  "Registering for a government housing subsidy.", "CAPITAL", False),
    (6,  "Booking a telehealth consultation with a doctor.", "CAPITAL", False),
    (7,  "Paying the monthly electricity bill online.", "CAPITAL", False),
    (8,  "Submitting a visa application form online.", "CAPITAL", False),
    (9,  "Ordering a prescription refill from the pharmacy website.", "CAPITAL", False),
    (10, "Signing an employment contract electronically.", "CAPITAL", False),
    (11, "Applying for a bank loan online.", "CAPITAL", False),
    (12, "Submitting a university enrollment form.", "CAPITAL", False),
    (13, "Renewing a driver's license on the government site.", "CAPITAL", False),
    (14, "Depositing a paycheck through the mobile banking app.", "CAPITAL", False),
    (15, "Filing a medical reimbursement claim with the insurer.", "CAPITAL", False),
    (16, "Scheduling a job interview by email.", "CAPITAL", False),
    (17, "Registering a small business on the official portal.", "CAPITAL", False),
    (18, "Updating tax withholding details on the payroll system.", "CAPITAL", False),
    (19, "Booking a vaccination appointment on the health portal.", "CAPITAL", False),
    (20, "Uploading documents to a mortgage application.", "CAPITAL", False),
    # ---- LEISURE (a): info-seeking / self-learning — COUNTERINTUITIVE (10) ----
    (21, "Reading the morning news about the national economy.", "LEISURE", True),
    (22, "Looking up the symptoms of high blood pressure.", "LEISURE", True),
    (23, "Watching a YouTube tutorial about Excel formulas.", "LEISURE", True),
    (24, "Searching for scholarship and admission information.", "LEISURE", True),
    (25, "Watching a documentary explainer on climate policy.", "LEISURE", True),
    (26, "Practicing English on a language-learning app.", "LEISURE", True),
    (27, "Researching farming techniques to raise crop yield.", "LEISURE", True),
    (28, "Reading product reviews before deciding on a laptop.", "LEISURE", True),
    (29, "Looking up legal information about a rental contract.", "LEISURE", True),
    (30, "Checking the weather forecast for the week.", "LEISURE", True),
    # ---- LEISURE (b): entertainment — intuitive (10) ----
    (31, "Playing a mobile battle game with friends.", "LEISURE", False),
    (32, "Scrolling short videos on Douyin for an hour.", "LEISURE", False),
    (33, "Streaming a romance drama series in the evening.", "LEISURE", False),
    (34, "Chatting with friends about weekend plans.", "LEISURE", False),
    (35, "Listening to a music playlist while relaxing.", "LEISURE", False),
    (36, "Reading celebrity gossip news.", "LEISURE", False),
    (37, "Watching a live-stream of a gaming influencer.", "LEISURE", False),
    (38, "Watching highlight clips of a football match.", "LEISURE", False),
    (39, "Playing casual puzzle games to pass time.", "LEISURE", False),
    (40, "Watching a K-pop music video.", "LEISURE", False),
]

# Deterministic split. Each split carries counterintuitive items; the train LEISURE
# items are ordered so the first few are counterintuitive (so small contrastive
# batches surface the narrow rule).
TEST = {1, 6, 9, 13, 17, 20, 22, 24, 25, 28, 33, 40}   # 6 CAP + (4 counter + 2 enter)
VAL = {3, 11, 15, 19, 29, 30, 35, 39}                  # 4 CAP + (2 counter + 2 enter)
# train = the rest: 10 CAP + (4 counter: 21,23,26,27) + (6 enter)

# Order train LEISURE so counterintuitive items come first.
TRAIN_LEISURE_ORDER = [21, 23, 26, 27, 31, 32, 34, 36, 37, 38]


def split_of(i):
    if i in TEST:
        return "test"
    if i in VAL:
        return "val"
    return "train"


def sort_key(row):
    i, _t, label, _c = row
    s = split_of(i)
    if s == "train" and label == "LEISURE":
        return (0, TRAIN_LEISURE_ORDER.index(i))
    return (0, i)


def main():
    out = os.path.join(HERE, "synthetic_data.csv")
    # keep a stable id order in the file, but the optimizer orders train LEISURE itself
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "label", "counterintuitive", "split"])
        for i, text, label, counter in ROWS:
            w.writerow([i, text, label, int(counter), split_of(i)])

    print(f"Wrote {out}  (total {len(ROWS)})")
    for s in ("train", "val", "test"):
        labs = Counter(label for i, _t, label, _c in ROWS if split_of(i) == s)
        nctr = sum(1 for i, _t, _l, c in ROWS if split_of(i) == s and c)
        print(f"  {s}: {dict(labs)}  counterintuitive={nctr}")

    with open(os.path.join(HERE, "CODEBOOK.md"), "w") as f:
        f.write(__doc__.strip() + "\n\n## Counterintuitive items "
                "(a vague prompt is expected to mislabel these as CAPITAL)\n\n")
        for i, text, label, counter in ROWS:
            if counter:
                f.write(f"- `{i}` gold=**{label}** — {text}\n")
    print("Wrote CODEBOOK.md")


if __name__ == "__main__":
    main()
