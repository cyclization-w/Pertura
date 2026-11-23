"""Papers for a query, from Europe PMC (PubMed and preprints such as bioRxiv), as prior knowledge.

Europe PMC's REST search returns, for each paper, its title, first author, journal, year, PMID or DOI and the start
of its abstract. A query's words are matched in titles and abstracts first, and the papers that leaves short are
filled from the full text, without meeting-abstract books; each paper says where it matched. Matched in the full text
alone, a query of many words finds the books that hold every word somewhere (Pertura's searches of 9 to 18 words on
GSE250558 returned little else). A PMID or a DOI alone looks that paper up. Papers the workspace keeps
out are dropped before the agent sees them. A test session keeps out its own screen's papers (literature_excluded:
{"pmids", "dois", "title_words"}), so that it cannot read the findings it is scored on. The analysis records what it
uses beside a finding or a thread, as prior knowledge, which informs and never changes a finding.
"""
import json
import re
import time
import urllib.parse
import urllib.request

URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
TRIES, PAUSE = 3, 2.0  # a request that timed out is asked again, so a slow service is not read as no papers
ABSTRACT_CHARS = 600
FEW_WORDS = 4  # beyond this, a query held whole by no title or abstract gets a hint
IN_TITLES, IN_FULL_TEXT = "title or abstract", "full text only"
FIELDS = ("AUTH", "TITLE", "ABSTRACT", "TITLE_ABS", "JOURNAL", "PUB_YEAR", "SRC", "EXT_ID", "DOI", "KW", "PUB_TYPE",
          "AFF")  # Europe PMC's fields an agent may write itself


class LiteratureError(Exception):
    pass


class NoAnswer(LiteratureError):
    """The service did not answer: the search did not run, which is not a search that found nothing."""


def _timed_out(e):
    return isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)


def _paper(r):
    journal = (((r.get("journalInfo") or {}).get("journal") or {}).get("title")
               or ("preprint" if r.get("source") == "PPR" else ""))
    authors = str(r.get("authorString") or "")
    abstract = " ".join(str(r.get("abstractText") or "").split())
    return {"id": f"PMID:{r['pmid']}" if r.get("pmid") else (f"DOI:{r['doi']}" if r.get("doi") else str(r.get("id"))),
            "pmid": r.get("pmid"), "doi": r.get("doi"), "title": str(r.get("title") or "").strip(),
            "first_author": authors.split(",")[0].strip(), "journal": journal, "year": r.get("pubYear"),
            "preprint": r.get("source") == "PPR",
            "abstract": abstract[:ABSTRACT_CHARS] + ("..." if len(abstract) > ABSTRACT_CHARS else "")}


def kept_out(paper, excluded):
    """Whether the workspace keeps this paper out: its PMID or DOI listed, or a listed phrase in its title."""
    ex = excluded or {}
    pmids = {str(x) for x in ex.get("pmids") or []}
    dois = {str(x).lower() for x in ex.get("dois") or []}
    title = paper["title"].lower()
    return (str(paper.get("pmid") or "") in pmids or str(paper.get("doi") or "").lower() in dois
            or any(str(w).lower() in title for w in ex.get("title_words") or []))


def queries(words):
    """Europe PMC's queries for the agent's words, in order, each with where its papers matched (None for a lookup):
    a PMID or a DOI alone looks that paper up, the agent's own field syntax (AUTH:, TITLE:) goes as written, and other
    words are matched in titles and abstracts, then in the full text without meeting-abstract books."""
    pmid = re.fullmatch(r"(?:pmid[:\s]*)?(\d{5,9})", words, re.I)
    if pmid:
        return [(f"EXT_ID:{pmid.group(1)} AND SRC:MED", None)]
    doi = re.fullmatch(r"(?:doi[:\s]*)?(10\.\d{4,9}/\S+)", words, re.I)
    if doi:
        return [(f'DOI:"{doi.group(1)}"', None)]
    if re.search(r"\bpmid[:\s]*\d|\b10\.\d{4,9}/", words, re.I):  # a PMID among words finds nothing
        raise LiteratureError("one query a call: a PMID or a DOI alone looks that paper up, and words go in a call of "
                              "their own")
    if re.search(r"\b(?:%s):" % "|".join(FIELDS), words):
        return [(words, None)]
    return [(f"TITLE_ABS:({words})", IN_TITLES), (f'({words}) NOT PUB_TYPE:"abstract"', IN_FULL_TEXT)]


def search(query, max_results=10, excluded=None, timeout=20, opener=None):
    """{"query", "papers": [...], "withheld": n, and hint} for a query; the papers the workspace keeps out are
    withheld."""
    query = str(query or "").strip()
    if not query:
        raise LiteratureError("give a query: genes, a pathway, a kind of screen")
    n = max(1, min(int(max_results or 10), 25))
    opener = opener or urllib.request.urlopen  # looked up at the call, so a test can stand in for it
    papers, seen, withheld = [], set(), 0
    asked = queries(query)
    for q, where in asked:
        if len(papers) >= n:
            break
        params = urllib.parse.urlencode({"query": q, "format": "json", "resultType": "core", "pageSize": n + 5})
        for attempt in range(TRIES):
            try:
                with opener(f"{URL}?{params}", timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as e:  # the service did not answer: said, never guessed
                if _timed_out(e) and attempt + 1 < TRIES:
                    time.sleep(PAUSE * (attempt + 1))
                    continue
                raise NoAnswer(f"Europe PMC did not answer ({type(e).__name__}: {e})")
        for r in (data.get("resultList") or {}).get("result") or []:
            p = _paper(r)
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            if kept_out(p, excluded):
                withheld += 1
            else:
                papers.append({**p, "matched": where} if where else p)
    out = {"query": query, "papers": papers[:n], "withheld": withheld}
    many = len(query.split())
    if asked[0][1] == IN_TITLES and many > FEW_WORDS and not any(p.get("matched") == IN_TITLES for p in papers):
        out["hint"] = (f"no title or abstract holds all {many} words, so the papers here match in the full text only, "
                       "where the relation may not be stated: search two to four words, such as two genes, or a gene "
                       "and a pathway")
    return out
