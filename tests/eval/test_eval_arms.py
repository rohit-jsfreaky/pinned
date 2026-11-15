"""Arms: none / docs (plain-docs baseline, cached, cleaned, truncated) / graph(search)."""

import json

from pinned.contract import Context, ContextItem, Hop
from pinned.eval.arms import DocsArm, GraphArm, NoneArm
from pinned.eval.docs import DocsFetcher, html_to_text

from eval_fakes import make_problem

HTML = """<html><head><title>T</title><script>var x=1;</script></head><body>
<nav>Home | Next</nav><div role="main"><h1>scipy.special.i1<a class="headerlink" href="#">¶</a></h1>
<p>Modified Bessel function of order 1.</p><pre>i1(x)\n  x : array_like</pre>
<p>Second paragraph that is long enough to make the main region win over everything else on the page.</p>
<p>More text so the main content is clearly longer than two hundred characters in total, as real pages are.</p>
</div><footer>Copyright</footer></body></html>"""


def test_html_to_text_keeps_content_drops_chrome():
    t = html_to_text(HTML)
    assert "scipy.special.i1" in t and "Modified Bessel function of order 1." in t
    assert "i1(x)\n  x : array_like" in t                 # <pre> keeps its layout
    assert "var x" not in t and "Home | Next" not in t and "Copyright" not in t and "¶" not in t


def _seed_cache(fetcher: DocsFetcher, url: str, body: str, status: int = 200) -> None:
    fetcher.cache_dir.mkdir(parents=True, exist_ok=True)
    fetcher._path(url).write_text(json.dumps({"url": url, "status": status, "content_type": "text/html",
                                              "body": body}))


def test_docs_arm_uses_cache_truncates_and_labels(tmp_path):
    f = DocsFetcher(tmp_path, offline=True)
    p = make_problem("1", docs=("https://d/a.html", "https://d/b.html", "https://d/missing.html"))
    _seed_cache(f, "https://d/a.html", HTML)
    _seed_cache(f, "https://d/b.html", "<html><body><main>" + ("word " * 2000) + "</main></body></html>")
    _seed_cache(f, "https://d/missing.html", "", 404)
    ctx = DocsArm(f, budget_chars=1000).context(p)
    assert ctx.text.startswith("Reference documentation (plain text of the official docs")
    assert "Modified Bessel function" in ctx.text and "[...truncated]" in ctx.text
    assert len(ctx.text) < 1000 + 400
    assert ctx.meta["urls"] == 3 and ctx.meta["fetched"] == 2 and ctx.meta["failed"] == ["https://d/missing.html"]
    assert ctx.context is None


def test_docs_arm_without_pages_gives_no_block(tmp_path):
    ctx = DocsArm(DocsFetcher(tmp_path, offline=True)).context(make_problem("1", docs=("https://d/x",)))
    assert ctx.text == ""


def test_github_blob_urls_fetch_the_raw_file():
    assert DocsFetcher.fetch_url("https://github.com/o/r/blob/main/CHANGES.md") == \
        "https://raw.githubusercontent.com/o/r/main/CHANGES.md"
    assert DocsFetcher.fetch_url("https://docs.x/y.html") == "https://docs.x/y.html"


def test_none_arm_is_empty():
    assert NoneArm().context(make_problem("1")).text == ""


class FakeSearch:
    def __init__(self):
        self.calls = []

    def context_for(self, problem, starting_code, library, version, k=8):
        self.calls.append((problem, starting_code, library, version, k))
        return Context(library, version, [ContextItem(library, "lib.new_f", "valid", [Hop("lib.old_f", "REPLACED_BY",
                                                                                          "lib.new_f")])])

    def check_calls(self, code, library, version):
        return []

    def explain_api(self, library, path):
        raise NotImplementedError


def test_graph_arm_calls_search_and_uses_to_prompt():
    s = FakeSearch()
    p = make_problem("1", "lib", "2.0")
    ctx = GraphArm(s, k=5).context(p)
    assert s.calls == [(p.problem, p.starting_code, "lib", "2.0", 5)]
    assert ctx.text == ctx.context.to_prompt()
    assert "lib.old_f -REPLACED_BY-> lib.new_f" in ctx.text
