#!/usr/bin/env python3
# board #913 owner-paper-quote-header
import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://en.wikiquote.org/w/api.php?action=parse&prop=wikitext&redirects=1&format=json&formatversion=2&page={}"
UA = "Rosie morning paper quote list builder (personal, one-time; contact: owner)"
PER_PERSON = 15
MIN_LEN, MAX_LEN = 30, 200

PEOPLE = [
    ("Marie Curie", "Marie Curie", "physicist and chemist"), ("Richard Feynman", "Richard Feynman", "physicist"),
    ("Emmy Noether", "Emmy Noether", "mathematician"), ("Srinivasa Ramanujan", "Srinivasa Ramanujan", "mathematician"),
    ("Ada Lovelace", "Ada Lovelace", "mathematician"), ("Grace Hopper", "Grace Hopper", "computer scientist"),
    ("Alan Turing", "Alan Turing", "mathematician"), ("Carl Sagan", "Carl Sagan", "astronomer"),
    ("Hypatia", "Hypatia", "mathematician"), ("Nikola Tesla", "Nikola Tesla", "inventor and engineer"),
    ("Michael Faraday", "Michael Faraday", "physicist and chemist"), ("Albert Einstein", "Albert Einstein", "physicist"),
    ("Katherine Johnson", "Katherine Johnson", "mathematician"), ("Henri Poincaré", "Henri Poincaré", "mathematician"),
    ("Leonhard Euler", "Leonhard Euler", "mathematician"), ("Isaac Newton", "Isaac Newton", "physicist and mathematician"),
    ("Galileo Galilei", "Galileo Galilei", "astronomer and physicist"), ("Charles Darwin", "Charles Darwin", "naturalist"),
    ("Rosalind Franklin", "Rosalind Franklin", "chemist"), ("Lise Meitner", "Lise Meitner", "physicist"),
    ("Niels Bohr", "Niels Bohr", "physicist"), ("Max Planck", "Max Planck", "physicist"), ("Johannes Kepler", "Johannes Kepler", "astronomer"),
    ("Louis Pasteur", "Louis Pasteur", "chemist and microbiologist"), ("Thomas Edison", "Thomas Edison", "inventor"),
    ("Alexander Graham Bell", "Alexander Graham Bell", "inventor"), ("Orville Wright", "Orville Wright", "aviation pioneer"),
    ("Wilbur Wright", "Wilbur Wright", "aviation pioneer"), ("Werner Heisenberg", "Werner Heisenberg", "physicist"),
    ("Erwin Schrödinger", "Erwin Schrödinger", "physicist"), ("Paul Dirac", "Paul Dirac", "physicist"),
    ("John von Neumann", "John von Neumann", "mathematician"), ("Claude Shannon", "Claude Shannon", "mathematician and engineer"),
    ("Edsger W. Dijkstra", "Edsger W. Dijkstra", "computer scientist"), ("Donald Knuth", "Donald Knuth", "computer scientist"),
    ("Alan Kay", "Alan Kay", "computer scientist"), ("Margaret Hamilton (software engineer)", "Margaret Hamilton", "software engineer"),
    ("Hedy Lamarr", "Hedy Lamarr", "actress and inventor"), ("Jane Goodall", "Jane Goodall", "primatologist"),
    ("Rachel Carson", "Rachel Carson", "biologist"), ("Barbara McClintock", "Barbara McClintock", "geneticist"),
    ("Chien-Shiung Wu", "Chien-Shiung Wu", "physicist"), ("Mae Jemison", "Mae Jemison", "engineer and astronaut"),
    ("Neil deGrasse Tyson", "Neil deGrasse Tyson", "astrophysicist"), ("Stephen Hawking", "Stephen Hawking", "physicist"),
    ("Jacob Bronowski", "Jacob Bronowski", "mathematician"), ("Isaac Asimov", "Isaac Asimov", "biochemist and writer"),
    ("Buckminster Fuller", "Buckminster Fuller", "engineer and inventor"), ("Benjamin Franklin", "Benjamin Franklin", "scientist and inventor"),
    ("James Clerk Maxwell", "James Clerk Maxwell", "physicist"), ("Ernest Rutherford", "Ernest Rutherford", "physicist"),
    ("Dmitri Mendeleev", "Dmitri Mendeleev", "chemist"), ("Alfred Russel Wallace", "Alfred Russel Wallace", "naturalist"),
    ("Edwin Hubble", "Edwin Hubble", "astronomer"), ("Vera Rubin", "Vera Rubin", "astronomer"),
    ("Carl Friedrich Gauss", "Carl Friedrich Gauss", "mathematician"), ("David Hilbert", "David Hilbert", "mathematician"),
    ("G. H. Hardy", "G. H. Hardy", "mathematician"), ("Paul Erdős", "Paul Erdős", "mathematician"), ("Kurt Gödel", "Kurt Gödel", "mathematician"),
    ("Blaise Pascal", "Blaise Pascal", "mathematician"), ("Pierre-Simon Laplace", "Pierre-Simon Laplace", "mathematician"),
    ("Archimedes", "Archimedes", "mathematician and engineer"), ("Leonardo da Vinci", "Leonardo da Vinci", "artist and engineer"),
    ("Linus Pauling", "Linus Pauling", "chemist"), ("Francis Crick", "Francis Crick", "biologist"), ("Freeman Dyson", "Freeman Dyson", "physicist"),
    ("Richard Hamming", "Richard Hamming", "mathematician"), ("Seymour Papert", "Seymour Papert", "mathematician and educator"),
    ("Tim Berners-Lee", "Tim Berners-Lee", "computer scientist"), ("Steve Wozniak", "Steve Wozniak", "engineer"),
    ("Dennis Ritchie", "Dennis Ritchie", "computer scientist"), ("Linus Torvalds", "Linus Torvalds", "software engineer"),
    ("Guglielmo Marconi", "Guglielmo Marconi", "radio pioneer"), ("Heinrich Hertz", "Heinrich Hertz", "physicist"),
    ("Robert H. Goddard", "Robert H. Goddard", "rocket engineer"), ("Sally Ride", "Sally Ride", "physicist and astronaut"),
    ("Mary Somerville", "Mary Somerville", "scientist and writer"), ("Sofia Kovalevskaya", "Sofia Kovalevskaya", "mathematician"),
    ("Maryam Mirzakhani", "Maryam Mirzakhani", "mathematician"), ("Terence Tao", "Terence Tao", "mathematician"),
    ("William Thomson, 1st Baron Kelvin", "Lord Kelvin", "physicist"), ("Robert Hooke", "Robert Hooke", "scientist"),
    ("Humphry Davy", "Humphry Davy", "chemist"), ("Alexander von Humboldt", "Alexander von Humboldt", "naturalist"),
    ("Nicolaus Copernicus", "Nicolaus Copernicus", "astronomer"), ("Subrahmanyan Chandrasekhar", "Subrahmanyan Chandrasekhar", "astrophysicist"),
    ("C. V. Raman", "C. V. Raman", "physicist"), ("Abdus Salam", "Abdus Salam", "physicist"),
    ("A. P. J. Abdul Kalam", "A. P. J. Abdul Kalam", "aerospace scientist"), ("George Washington Carver", "George Washington Carver", "scientist and inventor"),
    ("Charles Babbage", "Charles Babbage", "mathematician and inventor"), ("Hermann Weyl", "Hermann Weyl", "mathematician"),
    ("Norbert Wiener", "Norbert Wiener", "mathematician"), ("John Archibald Wheeler", "John Archibald Wheeler", "physicist"),
    ("Enrico Fermi", "Enrico Fermi", "physicist"), ("Jocelyn Bell Burnell", "Jocelyn Bell Burnell", "astrophysicist"),
    ("J. Robert Oppenheimer", "J. Robert Oppenheimer", "physicist"), ("Thomas Henry Huxley", "Thomas Henry Huxley", "biologist"),
    ("Gottfried Wilhelm Leibniz", "Gottfried Wilhelm Leibniz", "mathematician"), ("Joseph Fourier", "Joseph Fourier", "mathematician"),
    ("Sophie Germain", "Sophie Germain", "mathematician"), ("Florence Nightingale", "Florence Nightingale", "nurse and statistician"),
    ("Hans Christian Ørsted", "Hans Christian Ørsted", "physicist"), ("Rosalind Picard", "Rosalind Picard", "engineer"),
    ("Frances Arnold", "Frances Arnold", "chemical engineer"), ("Donna Strickland", "Donna Strickland", "physicist"),
    ("Jennifer Doudna", "Jennifer Doudna", "biochemist"), ("Edwin Howard Armstrong", "Edwin H. Armstrong", "radio engineer"),
    ("John Bardeen", "John Bardeen", "physicist"), ("Gordon Moore", "Gordon Moore", "engineer"), ("Hermann von Helmholtz", "Hermann von Helmholtz", "physicist"),
    ("Antoine Lavoisier", "Antoine Lavoisier", "chemist"), ("Arthur Eddington", "Arthur Eddington", "astrophysicist"),
    ("Edward O. Wilson", "E. O. Wilson", "biologist"), ("Lynn Margulis", "Lynn Margulis", "biologist"), ("Kip Thorne", "Kip Thorne", "physicist"),
    ("Brian Greene", "Brian Greene", "physicist"), ("Lisa Randall", "Lisa Randall", "physicist"), ("Steven Weinberg", "Steven Weinberg", "physicist"),
    ("Murray Gell-Mann", "Murray Gell-Mann", "physicist"), ("Edward Witten", "Edward Witten", "physicist"),
    ("George Pólya", "George Pólya", "mathematician"), ("Paul Halmos", "Paul Halmos", "mathematician"),
    ("Benoit Mandelbrot", "Benoit Mandelbrot", "mathematician"), ("Andrew Wiles", "Andrew Wiles", "mathematician"),
    ("Max Born", "Max Born", "physicist"), ("Ludwig Boltzmann", "Ludwig Boltzmann", "physicist"), ("Jonas Salk", "Jonas Salk", "virologist"),
    ("Alexander Fleming", "Alexander Fleming", "biologist"), ("Santiago Ramón y Cajal", "Santiago Ramón y Cajal", "neuroscientist"),
    ("Oliver Sacks", "Oliver Sacks", "neurologist"), ("Peter Medawar", "Peter Medawar", "biologist"),
    ("Fred Brooks", "Fred Brooks", "computer scientist"), ("Bjarne Stroustrup", "Bjarne Stroustrup", "computer scientist"),
    ("Vint Cerf", "Vint Cerf", "computer scientist"), ("Cecilia Payne-Gaposchkin", "Cecilia Payne-Gaposchkin", "astronomer"),
    ("William Herschel", "William Herschel", "astronomer"), ("Christiaan Huygens", "Christiaan Huygens", "physicist and astronomer"),
    ("Pierre Curie", "Pierre Curie", "physicist"),
]

SKIP_HEAD = re.compile(r"disput|misattribut|attributed|unsourced|apocryph|dubious|about|see also|external|reference|sources|notes|"
                       r"bibliograph|further reading|works about|quoted by|said of|tributes|obituar|wrongly", re.I)
SKIP_CITE = re.compile(r"\battributed\b|\bdisputed\b|unsourced|misattribut|apocryph|\bunverified\b|\bdubious\b", re.I)
COMPILATION = re.compile(r"reader's digest|speaker's|quotebook|quotations|quotable|treasury|dictionary of|book of quotes|"
                         r"wit and wisdom|new words|redefine|inspir|success|leadership|motivat", re.I)
FICTION = re.compile(r"\bseries\b|\bnovel\b|\bshort story\b|\bstories\b|\bplay\b|\bpoem\b|religio|watchtower|apostle|bible|\breview of\b|"
                     r"\bfoundation\b|\bazazel\b|\bfiction\b", re.I)
NEGATIVE = re.compile(r"\b(war|wars|kill\w*|murder\w*|bomb\w*|weapon\w*|dead|death|die|dies|dying|hate\w*|stupid\w*|idiot\w*|fool\w*|damn\w*|hell|"
                      r"god|gods|religio\w*|church|bible|pray\w*|sin|soul\w*|politic\w*|govern\w*|nation\w*|race|racial|negro\w*|jew\w*|"
                      r"christian\w*|muslim\w*|sex\w*|women|woman|wife|husband|girls?|boys?|drunk\w*|money|"
                      r"atom bomb|nazi\w*|hitler|enemy|enemies|suicide|cancer|disease|poison\w*|blood|cruel\w*|christ|jesus|heaven\w*|"
                      r"divine|faith|creator|lord|spirit\w*|holy|said|says|evil|drink\w*|alcohol\w*|traitor\w*|unto|thee|thou|thy|worms?)\b", re.I)


def clean(t: str) -> str:
    t = re.sub(r"(?s)<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", t)
    t = re.sub(r"(?s)<!--.*?-->", "", t)
    for _ in range(3):
        t = re.sub(r"\{\{\s*(?:w|wikipedia|wp)\s*\|\s*([^|{}]+?)\s*(?:\|\s*([^{}]+?))?\s*\}\}", lambda m: m.group(2) or m.group(1), t, flags=re.I)
        t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[(?:https?:)?//\S+\s+([^\]]*)\]", r"\1", t)
    t = re.sub(r"\[(?:https?:)?//\S+\]", "", t)
    t = re.sub(r"'{2,}", "", t)
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


EN_WORDS = set("the a an and of to in is it that we you i our be are was for not can this with what as by have all one more no if or "
               "but my there its from do so they he she his her".split())
OTHER_WORDS = set("der die das und ist mit nicht ein eine les la le et est une des du que qui il je nous dans pour pas sur el los las "
                  "es un una por con como del se lo".split())
UPLIFT = re.compile(r"\b(learn\w*|know\w*|discover\w*|curio\w*|wonder\w*|imagin\w*|scien\w*|idea\w*|question\w*|try|tried|trying|"
                    r"work\w*|dream\w*|creat\w*|understand\w*|nature|truth|beaut\w*|explor\w*|think\w*|thought\w*|mind\w*|future|"
                    r"possib\w*|impossib\w*|problem\w*|solv\w*|mistake\w*|fail\w*|patien\w*|persever\w*|persist\w*|courage\w*|"
                    r"believe\w*|joy\w*|mathemat\w*|invent\w*|experiment\w*|observ\w*|research\w*|teach\w*|educat\w*|study|"
                    r"simple|simplicity|universe|stars?|sky|light|life|world|hope\w*|begin\w*|start\w*|build\w*|engineer\w*|"
                    r"progress\w*|new|nothing|everything|great\w*|wisdom|wise|reason\w*|doubt\w*|ignoran\w*|answer\w*|seek\w*|"
                    r"search\w*|fear\w*|chance|effort\w*|genius|talent\w*|play\w*|fun|delight\w*|pleasure|marvel\w*|mystery|mysteri\w*)\b", re.I)


def english(t: str) -> bool:
    if sum(1 for c in t if ord(c) < 128 or c in "’‘“”—–…é") < 0.97 * len(t):
        return False
    ws = re.findall(r"[a-z']+", t.lower())
    return bool(ws) and not any(w in OTHER_WORDS for w in ws) and sum(w in EN_WORDS for w in ws) >= max(1, len(ws) // 8)


def parse(wikitext: str) -> list[dict]:
    out, heads, cur = [], {}, None
    lines = wikitext.splitlines()

    def skipped():
        return any(SKIP_HEAD.search(h) for h in heads.values())

    def flush():
        if cur and (cur["cites"] or work_heading(cur["heads"])):
            out.append(cur)
    for ln in lines:
        m = re.match(r"^(={2,6})\s*(.*?)\s*\1\s*$", ln)
        if m:
            flush()
            cur = None
            lvl = len(m.group(1))
            heads = {k: v for k, v in heads.items() if k < lvl}
            heads[lvl] = clean(m.group(2))
            continue
        if skipped():
            continue
        if re.match(r"^\*[^*:]", ln):
            flush()
            cur = {"quote": clean(ln[1:]), "cites": [], "section": " / ".join(heads[k] for k in sorted(heads)), "heads": dict(heads)}
        elif re.match(r"^\*\*[^*]", ln) and cur is not None:
            cur["cites"].append(clean(ln[2:]))
    flush()
    return out


def work_heading(heads: dict) -> str:
    for k in sorted(heads, reverse=True):
        h = heads[k]
        if re.search(r"\b(1\d{3}|20\d{2})\b", h) and len(re.findall(r"[A-Za-z]{3,}", h)) >= 2 and not SKIP_HEAD.search(h):
            return h
    return ""


def pick_cite(q: dict) -> str | None:
    wh = work_heading(q["heads"])
    for c in q["cites"]:
        if not c or SKIP_CITE.search(c) or c.lower().startswith(("original", "variant", "translation", "see also", "note")):
            continue
        if not english(c) or len(c) > 220:
            continue
        if re.search(r"\b(1\d{3}|20\d{2})\b", c):
            return c
        if wh:
            return f"{wh}, {c}" if len(wh) + len(c) < 220 else None
    if not q["cites"] and wh and len(wh) <= 220 and english(wh):
        return wh
    return None


def good_quote(t: str) -> bool:
    return (MIN_LEN <= len(t) <= MAX_LEN and english(t) and not NEGATIVE.search(t) and bool(UPLIFT.search(t)) and t[0].isupper()
            and not SKIP_CITE.search(t) and not re.search(r'["“”]', t) and "http" not in t and not t.endswith((":", ",", ";"))
            and not re.search(r"[=^]|\b[a-z]\s*[+<>]\s*[a-z]\b", t)
            and not re.search(r"(\.\.\.|…)\s*\S", t) and t.rstrip()[-1] in ".!?…"  # board #913 why-whole-sentences-only
            and not re.search(r"[\[\]]|\(\d\)|\b(bums?|cranks?|pity|wretched|miser\w*)\b", t, re.I))


def fetch(cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    for page, _, _ in PEOPLE:
        f = cache / (re.sub(r"[^A-Za-z0-9]+", "_", page) + ".json")
        if f.exists():
            continue
        req = urllib.request.Request(API.format(urllib.parse.quote(page)), headers={"User-Agent": UA})
        for attempt in range(4):
            try:
                f.write_bytes(urllib.request.urlopen(req, timeout=30).read())
                break
            except urllib.error.HTTPError as e:  # board #913 quirk-rate-limit-retry
                if e.code != 429:
                    print("skip", page, e, file=sys.stderr)
                    break
                time.sleep(float(e.headers.get("Retry-After") or 30) + 5)
            except Exception as e:
                print("skip", page, e, file=sys.stderr)
                break
        time.sleep(3.0)


def build(cache: Path, out: Path):
    rows, seen = [], set()
    for page, name, role in PEOPLE:
        f = cache / (re.sub(r"[^A-Za-z0-9]+", "_", page) + ".json")
        j = json.loads(f.read_bytes() or b"{}") if f.exists() else {}
        wt = ((j.get("parse") or {}).get("wikitext")) or ""
        title = (j.get("parse") or {}).get("title") or page
        if not wt:
            continue
        n = 0
        for q in parse(wt):
            t = q["quote"].strip().strip('"“”').strip()
            c = pick_cite(q)
            key = re.sub(r"\W+", "", t.lower())
            if not c or not good_quote(t) or key in seen or FICTION.search(c) or FICTION.search(q["section"]) or COMPILATION.search(c):
                continue
            seen.add(key)
            url = "https://en.wikiquote.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
            rows.append([t, name, role, c, q["section"], url])
            n += 1
            if n >= PER_PERSON:
                break
    out.write_text("".join("\t".join([f"q{i + 1:04d}"] + [x.replace("\t", " ") for x in r]) + "\n" for i, r in enumerate(rows)), encoding="utf-8")
    print(len(rows), "quotes from", len({r[1] for r in rows}), "people;", out, hashlib.sha256(out.read_bytes()).hexdigest())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["fetch", "build"])
    ap.add_argument("cache")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "app" / "paper_quotes.tsv"))
    a = ap.parse_args()
    if a.mode == "fetch":
        fetch(Path(a.cache))
    else:
        build(Path(a.cache), Path(a.out))
