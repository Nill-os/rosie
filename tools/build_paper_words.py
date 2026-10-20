#!/usr/bin/env python3
"""Builds app/paper_words.tsv (the Morning Paper's vocabulary crossword words) from Princeton WordNet 3.0.

Source: the nltk_data WordNet corpus, https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/wordnet.zip
(sha256 cbda5ea6eef7f36a97a43d4a75f85e07fccbb4f23657d27b4ccbc93e2646ab59, fetched 2026-10-13). WordNet licence: app/paper_words.LICENSE.

    python3 -I tools/build_paper_words.py <wordnet.zip> --cmudict <cmudict.dict> [--dict /usr/share/dict/american-english] [--out app/paper_words.tsv]

Pronunciation: the CMU Pronouncing Dictionary, https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict
(sha256 81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22, fetched 2026-10-13; licence: app/paper_words.CMUDICT-LICENSE).
A word's first CMU pronunciation is kept (ARPAbet) with an easy respelling made from it by paper_fun.respell ("ak-wee-ESS").
A word with no CMU entry is left out: a pronunciation is never guessed.

What gets picked ("words to learn"):
- plain lower-case single words of 5-9 letters (4-9 for the trade terms) that the system's English word list also has (no obscure
  or technical oddities);
- a frequency band from WordNet's own tag counts (cntlist.rev, how often each sense was seen in the SemCor texts): seen at least
  MIN_FREQ times (a real, used word) but at most MAX_FREQ times (not an everyday word you already know);
- at most MAX_SENSES meanings in all (WordNet's own familiarity measure: everyday words have many), so one definition is a fair clue;
- not a form built on an easier word (-ing, -ed, -ly, -ness, -er ...; re-, un-, over- ... on a word that exists by itself);
- never offensive, vulgar, slang or a slur (by WordNet's own wording and a short list), never a definition that gives the word away;
- general words: no specialist senses (a gloss that starts with a field like "(botany)"), no plants or animals, and no concrete
  everyday things (WordNet's artifact, food, body ... files); abstract nouns, verbs, adjectives and adverbs;
- plus business, tech and radio terms (TRADE below) that have a WordNet sense, with the sense whose definition fits the trade.
Each line: word, part of speech, WordNet's definition (gloss), a WordNet example sentence when it has one, kind (word|biz|tech|radio),
respelling, CMU phones.
Nothing is written by hand: every definition and example is WordNet's."""
import argparse
import collections
import io
import re
import sys
import zipfile
from pathlib import Path

MIN_FREQ, MAX_FREQ, MAX_SENSES = 1, 6, 3
DERIVED = re.compile(r"(ing|ed|ly|ness|less|ful|ish|ers?)$")
PREFIX = re.compile(r"^(re|un|non|over|under|out|pre|mis|dis|sub|super|inter|self)(?=[a-z]{4,})")
POS = {"n": ("noun", "noun"), "v": ("verb", "verb"), "a": ("adj", "adjective"), "s": ("adj", "adjective"), "r": ("adv", "adverb")}
SS_TYPE = {"1": "n", "2": "v", "3": "a", "4": "r", "5": "s"}
BAD_GLOSS = re.compile(r"\b(offensive|vulgar|obscen|slur|derogatory|disparag|slang|sexual|sex\b|genital|penis|vagina|copulat|prostitut|"
                       r"racist|pregnan|abort|behead|tortur|corpse|feces|excrement|urinat|defecat|buttocks|breast|kill|murder|suicide|rape|drug|narcotic|alcoholic|nazi|"
                       r"informal term|archaic|obsolete|dialect|british|chiefly|scottish|irish|euphemism|pejorative)", re.I)
BAD_WORDS = {"bastard", "bitch", "whore", "slut", "faggot", "nigger", "retard", "spastic", "cripple", "midget", "dyke", "pansy", "tranny"}
GENUS = re.compile(r"\b(genus|species|family [A-Z]|order [A-Z])\b")
# WordNet lexicographer files of concrete nouns (artifact, animal, body, food, object, plant, quantity, substance, location, Tops):
# everyday things are not "words to learn"; abstract nouns (acts, states, feelings, ideas, people) stay
CONCRETE = {3, 5, 6, 8, 13, 15, 17, 20, 23, 27}
TRADE = {
    "biz": "accrual amortize arbitrage attrition backorder benchmark bootstrap brokerage chargeback collateral commodity consignment "
           "depreciate dividend equity fiduciary insolvent inventory invoice leverage liability liquidity logistics markup overhead "
           "procure prospectus quota receivable remittance revenue solvency stipend subsidy surplus tariff turnover underwrite vendor "
           "wholesale margin audit capital ledger payroll rebate refund retail vendor franchise freight manifest customs duty broker "
           "invoice merchant",
    "tech": "algorithm bandwidth cache checksum compiler cipher daemon debug encrypt exploit firewall firmware heuristic kernel latency "
            "malware metadata protocol recursion sandbox throttle token virtualize database encryption password router server "
            "software hardware network processor semiconductor transistor binary pixel spreadsheet download upload",
    "radio": "amplitude antenna attenuate balun callsign coaxial decibel dipole duplex fading ferrite harmonic impedance ionosphere "
             "megahertz modulate oscillator propagate repeater resonance spectrum squelch telemetry waveform frequency transmitter "
             "receiver transceiver static wavelength kilohertz amplifier capacitor inductor resistor diode circuit voltage ammeter "
             "oscilloscope solder",
}
TRADE_HINT = {"biz": re.compile(r"\b(business|commerce|commercial|money|financ|account|pay|sale|sell|trade|goods|cost|price|profit|debt|tax|market|stock|company|merchant|customer|loan|ship)", re.I),
              "tech": re.compile(r"\b(computer|program|data|software|network|electronic|digital|code|memory|processor|information|system)", re.I),
              "radio": re.compile(r"\b(radio|signal|wave|frequenc|electric|current|circuit|antenna|transmi|receiv|voltage|oscillat|sound|cycle|ionized)", re.I)}


def read(z, name) -> list[str]:
    return io.TextIOWrapper(z.open(f"wordnet/{name}"), encoding="latin-1").read().splitlines()


def load(zpath):
    z = zipfile.ZipFile(zpath)
    freq = collections.Counter()
    for line in read(z, "cntlist.rev"):
        key, _, cnt = line.split()
        lemma, rest = key.split("%", 1)
        freq[(lemma, SS_TYPE[rest[0]])] += int(cnt)
    index = {}
    for p in ("noun", "verb", "adj", "adv"):
        for line in read(z, f"index.{p}"):
            if line.startswith(" "):
                continue
            f = line.split()
            lemma, pos, n_syn, n_ptr = f[0], f[1], int(f[2]), int(f[3])
            offsets = f[6 + n_ptr:]
            index[(lemma, pos)] = offsets[:n_syn]
    data = {}
    for p in ("noun", "verb", "adj", "adv"):
        for line in read(z, f"data.{p}"):
            if line.startswith(" "):
                continue
            off, lexnum = line[:8], int(line.split()[1])
            head, _, gloss = line.partition(" | ")
            data[(p, off)] = gloss.strip()
            data[(p, off, "lex")] = lexnum
    return freq, index, data


def split_gloss(gloss: str) -> tuple[str, list[str]]:
    examples = re.findall(r'"([^"]+)"', gloss)
    defn = gloss.split('; "')[0].split(' "')[0].strip().rstrip(";").strip()
    return defn, examples


def pick_example(word: str, examples: list[str]) -> str:
    stem = word[:max(4, len(word) - 2)]
    for e in examples:
        if re.search(rf"\b{re.escape(stem)}", e, re.I) and 15 <= len(e) <= 140:
            return e.strip()
    return ""


def fair(word: str, defn: str) -> bool:
    return 8 <= len(defn) <= 130 and word[:max(4, len(word) - 3)].lower() not in defn.lower() and not BAD_GLOSS.search(defn)


def entry(word, pos_key, offsets, data, kind, hint=None):
    p = {"n": "noun", "v": "verb", "a": "adj", "r": "adv"}[pos_key]
    for off in offsets:
        gloss = data.get((p, off))
        if not gloss or BAD_GLOSS.search(gloss):
            continue
        if kind == "word" and (gloss.startswith("(") or GENUS.search(gloss) or (p == "noun" and data[(p, off, "lex")] in CONCRETE)):
            continue                                              # a specialist's sense, a plant or animal, a thing you can point at
        defn, ex = split_gloss(gloss)
        if not fair(word, defn) or (hint and not hint.search(defn)):
            continue
        return {"word": word, "pos": POS[pos_key][1], "definition": defn, "example": pick_example(word, ex), "kind": kind}
        # only the first acceptable sense (WordNet lists the most frequent first)
    return None


def build(zpath, dict_path):
    freq, index, data = load(zpath)
    english = {w.strip() for w in Path(dict_path).read_text(errors="ignore").splitlines() if w.strip().isalpha() and w.strip().islower()}
    out = {}
    for kind, words in TRADE.items():
        for w in dict.fromkeys(words.split()):
            if not re.fullmatch(r"[a-z]{4,9}", w) or w in out:
                continue
            for pos_key in sorted("nvar", key=lambda k: -freq[(w, k)]):
                if (w, pos_key) in index:
                    e = entry(w, pos_key, index[(w, pos_key)], data, kind, TRADE_HINT[kind])
                    if e:
                        out[w] = e
                        break
    lemmas = collections.defaultdict(dict)
    for (lemma, pos), offs in index.items():
        lemmas[lemma][pos] = offs
    for w, by_pos in sorted(lemmas.items()):
        if w in out or w in BAD_WORDS or not re.fullmatch(r"[a-z]{5,9}", w) or w not in english:
            continue
        total = sum(freq[(w, k)] for k in "nvars")
        if not MIN_FREQ <= total <= MAX_FREQ:
            continue
        if sum(len(v) for v in by_pos.values()) > MAX_SENSES or DERIVED.search(w):
            continue                                              # many meanings = an everyday word; -ing / -ly / -ness = built on an easier one
        m = PREFIX.match(w)
        if m and w[m.end():] in english:                          # reopen, unhappy, overpay: the base word is the one to learn
            continue
        main = max(by_pos, key=lambda k: (freq[(w, k)] + (freq[(w, "s")] if k == "a" else 0), -len(by_pos[k])))
        e = entry(w, main, by_pos[main], data, "word")
        if e:
            out[w] = e
    return out


def load_cmudict(path) -> dict:
    """{word: first pronunciation} (alternates like "word(2)" and comments dropped)."""
    out = {}
    for line in Path(path).read_text(encoding="latin-1").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        w, _, ph = line.partition(" ")
        if "(" not in w and w not in out:
            out[w] = ph.strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--cmudict", required=True)
    ap.add_argument("--dict", default="/usr/share/dict/american-english")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "app" / "paper_words.tsv"))
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
    from paper_fun import respell
    words = build(a.zip, a.dict)
    cmu = load_cmudict(a.cmudict)
    no_entry = [w for w in words if w not in cmu]
    for w in no_entry:
        del words[w]
    for w, e in words.items():
        e["phones"] = cmu[w]
        e["say"] = respell(cmu[w])
    head = ["# Words to learn for the Morning Paper, built by tools/build_paper_words.py: words, definitions and examples from Princeton WordNet 3.0",
            "# (WordNet 3.0 Copyright 2006 by Princeton University; licence in app/paper_words.LICENSE); pronunciation from the CMU Pronouncing",
            "# Dictionary (Copyright 1993-2015 Carnegie Mellon University; licence in app/paper_words.CMUDICT-LICENSE). Nothing is written by hand.",
            "# word <TAB> part of speech <TAB> definition <TAB> example (may be empty) <TAB> kind (word|biz|tech|radio) <TAB> respelling <TAB> CMU phones"]
    rows = [f"{e['word']}\t{e['pos']}\t{e['definition']}\t{e['example']}\t{e['kind']}\t{e['say']}\t{e['phones']}"
            for e in sorted(words.values(), key=lambda e: e["word"])]
    print(f"left out {len(no_entry)} words with no CMU pronunciation", file=sys.stderr)
    Path(a.out).write_text("\n".join(head + rows) + "\n", encoding="utf-8")
    kinds = collections.Counter(e["kind"] for e in words.values())
    print(f"{len(rows)} words -> {a.out}  {dict(kinds)}", file=sys.stderr)


if __name__ == "__main__":
    main()
