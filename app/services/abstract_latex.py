"""The LaTeX an abstract is drawn with.

One abstract renders the same whether it is a page of the conference booklet
or a single PDF a presenter downloads to check their own submission — so the
fragment builder, the preamble and the image conversion live here rather than
inside the admin blueprint that happened to need them first.

Callers hand the finished tree to `documents.compile_prepared_dir`, which is
the one tectonic invocation.
"""
from __future__ import annotations

from pathlib import Path

# The LaTeX text-mode escape lives with the document renderer (single home for
# the escaping table); the abstract export reuses it.
from .citations import fetch_metadata, format_reference_compact
from .documents import latex_escape as _latex_escape


KNOWN_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}

# How an abstract page uses its space. Body text is deliberately absent from
# this list: its size, measure and leading are what make an abstract readable
# and are not somewhere to claw back room. Everything here is apparatus around
# it.
#
# A figure constrained in both dimensions keeps its aspect ratio inside that
# box, so a wide figure gets the full measure and a tall one is capped rather
# than swallowing the page.
FIGURE_MAX_WIDTH = "\\textwidth"
FIGURE_SPACE_ABOVE = "8pt"

# References are apparatus, not prose: smaller, single-spaced, and set close.
REF_FONT_SIZE = "\\footnotesize"
REF_TOP_SPACE = "10pt"
REF_ITEM_SPACE = "2pt"


def convert_for_latex(src: Path, dst: Path) -> Path:
    """Ensure *src* is a LaTeX-compatible image, writing to *dst* as needed.

    The booklet compiles with tectonic (XeTeX), which — like pdfLaTeX before
    it — reads PNG, JPG, and PDF natively.  WEBP and TIFF are *not* supported,
    so we transcode them to PNG.  If the source is already compatible we just
    copy the bytes.  Returns the destination path (may differ from *dst* if the
    suffix was changed).
    """
    from PIL import Image

    ext = src.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg"}:
        dst.write_bytes(src.read_bytes())
        return dst
    if ext == ".pdf":
        dst.write_bytes(src.read_bytes())
        return dst
    try:
        img = Image.open(src)
        img = img.convert("RGB")
        png_dst = dst.with_suffix(".png")
        img.save(png_dst, "PNG", optimize=True)
        return png_dst
    except Exception:
        dst.write_bytes(src.read_bytes())
        return dst


def booklet_preamble(conference, inputs: list[str],
                      header_rel: str | None,
                      footer_rel: str | None,
                      bg_rel: str | None) -> str:
    title_esc = _latex_escape(conference.title)
    date_esc = conference.date_range

    pkgs = [
        "\\documentclass[11pt,a4paper]{article}",
        "\\usepackage[margin=25.4mm,headheight=14pt,footskip=18pt]{geometry}",
        # NOTE: under tectonic (XeTeX) this pair is accepted and then
        # ignored — the document sets in Latin Modern Roman, not Helvetica,
        # as it did under pdflatex. `\\usepackage[T1]{fontenc}` restores it
        # but triggers a system-wide font scan that is both irreproducible
        # and too memory-hungry for the capped compile child. Left as-is
        # deliberately; see CHANGELOG.
        "\\usepackage{helvet}",
        "\\renewcommand{\\familydefault}{\\sfdefault}",
        "\\usepackage{setspace}",
        "\\setstretch{1.15}",
        "\\usepackage{graphicx}",
        "\\usepackage{hyperref}",
        "\\usepackage{parskip}",
        "\\usepackage{fancyhdr}",
    ]
    if bg_rel:
        pkgs.append("\\usepackage[pages=all]{background}")

    pkgs.append("")
    pkgs.append("\\pagestyle{fancy}")
    pkgs.append("\\fancyhf{}")
    pkgs.append("\\renewcommand{\\headrulewidth}{0.4pt}")

    if header_rel:
        pkgs.append(
            "\\fancyhead[L]{\\includegraphics[height=1.3cm,keepaspectratio]"
            f"{{{header_rel}}}}}"
        )
    else:
        pkgs.append(f"\\fancyhead[L]{{\\small\\itshape {title_esc}}}")
    pkgs.append("\\fancyhead[R]{\\small\\thepage}")

    if footer_rel:
        pkgs.append(
            "\\fancyfoot[R]{\\includegraphics[height=0.9cm,keepaspectratio]"
            f"{{{footer_rel}}}}}"
        )
    else:
        pkgs.append("\\fancyfoot[C]{}")

    if bg_rel:
        pkgs.append("\\backgroundsetup{")
        pkgs.append(f"  contents={{\\includegraphics[width=\\paperwidth,height=\\paperheight]{{{bg_rel}}}}},")
        pkgs.append("  opacity=0.06,")
        pkgs.append("  scale=1,")
        pkgs.append("}")

    pkgs.append("")
    pkgs.append(f"\\title{{{title_esc}}}")
    pkgs.append("\\author{Abstract Booklet}")
    pkgs.append(f"\\date{{{date_esc}}}")
    pkgs.append("")
    pkgs.append("\\begin{document}")
    pkgs.append("\\thispagestyle{empty}")
    if bg_rel:
        pkgs.append("\\NoBgThispage")
    pkgs.append("\\maketitle")
    pkgs.append("\\tableofcontents")
    pkgs.append("\\newpage")
    pkgs.append("")
    pkgs.extend(inputs)
    pkgs.append("")
    pkgs.append("\\end{document}")

    return "\n".join(pkgs)


def abstract_fragment(label: str, abstract,
                       has_header: bool = False,
                       has_background: bool = False) -> str:
    """Return LaTeX fragment matching the abstract preview page layout.

    Centred title (bold), centred authors with superscript affiliations
    and presenting author underlined, centred italic affiliations,
    career-stage / presentation-preference meta, justified body,
    figure filling remaining space, and numbered DOI references.
    """
    folder = f"abstract_{label}"
    title = _latex_escape(abstract.title)

    # The body keeps its own escaping pass rather than calling _latex_escape,
    # because the two differ on newlines: the shared one turns every single
    # newline into a forced break, while an abstract body wants LaTeX's normal
    # wrapping and only treats a blank line as a paragraph. The character
    # table below must still match it exactly — `_` was missing here, so any
    # body containing one ("TiO_2", a gene name, a filename) failed to compile
    # and took the whole booklet with it.
    body = abstract.body
    _BSL = "\x00BSL\x00"
    body = body.replace("\\", _BSL)
    body = body.replace("&", "\\&").replace("#", "\\#")
    body = body.replace("$", "\\$").replace("%", "\\%")
    body = body.replace("_", "\\_")
    body = body.replace("{", "\\{").replace("}", "\\}")
    body = body.replace("~", "\\textasciitilde{}").replace("^", "\\^{}")
    body = body.replace(_BSL, "\\textbackslash{}")
    body = body.replace("\r\n", "\n")
    body = body.replace("\n\n", "\n\n\\medskip\n\n")

    def _out_ext(filename: str | None) -> str:
        if not filename:
            return ""
        ext = Path(filename).suffix.lower()
        if ext in KNOWN_IMAGE_EXTS:
            return ".png" if ext in {".webp", ".tif", ".tiff"} else ext
        return ext

    presenting_idx = abstract.presenting_author_index or 0
    author_line, affil_line = parse_authors(abstract.authors, presenting_idx)

    lines: list[str] = []

    # Build TOC text: title + first author et al.
    toc_text = title
    first_author = ""
    if abstract.authors:
        first_line = abstract.authors.strip().split("\n")[0]
        first_name = first_line.split("|")[0].strip()
        if first_name:
            first_author = _latex_escape(first_name)
    if first_author:
        total = len([ln for ln in abstract.authors.strip().split("\n") if ln.strip()])
        if total > 1:
            toc_text = f"{title} --- {first_author} \\textit{{et al.}}"
        else:
            toc_text = f"{title} --- {first_author}"
    # \phantomsection first, or every contents entry links to the same place.
    # \addcontentsline records a title and a page, but the hyperlink it makes
    # points at the most recent anchor — and an abstract fragment issues no
    # sectioning command, so without this there is exactly one anchor in the
    # whole booklet and all of the entries jump to it.
    lines.append("\\phantomsection")
    lines.append(f"\\addcontentsline{{toc}}{{section}}{{{toc_text}}}")

    if has_background:
        lines.append("\\BgThispage")

    lines.append("\\begin{center}")
    lines.append(f"  {{\\LARGE\\bfseries {title}\\par}}")
    lines.append("\\end{center}")

    if author_line:
        lines.append("\\begin{center}")
        lines.append(f"  {{\\large {author_line}\\par}}")
        lines.append("\\end{center}")

    if affil_line:
        lines.append("\\begin{center}")
        lines.append(f"  {{\\large\\itshape {affil_line}\\par}}")
        lines.append("\\end{center}")

    cd = abstract.custom_data or {}
    career = (cd.get("career-stage") or "").strip()
    pres = (cd.get("presentation-preference") or "").strip()
    meta_bits: list[str] = []
    if career:
        meta_bits.append(career)
    if pres:
        meta_bits.append(pres)
    if meta_bits:
        lines.append("\\begin{center}")
        lines.append(f"  {{\\small\\textit{{{'  \\textperiodcentered{}  '.join(meta_bits)}}}\\par}}")
        lines.append("\\end{center}")

    lines.append("")
    lines.append(body)

    if abstract.figure_filename:
        out = _out_ext(abstract.figure_filename)
        # Room to keep clear for the reference block, so a figure that expands
        # into the space left on the page does not orphan the references onto
        # the next one.
        reserve = f"{len(abstract.references or []) + 2}\\baselineskip" \
            if abstract.references else "0pt"
        lines.append("")
        # The figure takes exactly the space left between the body and the
        # references, which is what keeps an abstract to one page. A fixed
        # height larger than the remainder would push the whole figure to the
        # next page and leave the gap empty. An author whose text leaves little
        # room gets a small figure, and can shorten the text if they would
        # rather have a large one.
        #
        # \pagetotal only counts what the page builder has already taken, so
        # the paragraph has to be ended and a breakpoint offered before the
        # remaining space can be measured. \pagegoal is \maxdimen until the
        # page builder has run at all, hence the clamp.
        lines.append("\\par")
        lines.append(f"\\vspace{{{FIGURE_SPACE_ABOVE}}}")
        lines.append("\\penalty0")
        lines.append("\\begingroup")
        lines.append(f"\\sbox0{{\\includegraphics{{{folder}/figure{out}}}}}")
        lines.append("\\dimen0=\\pagegoal")
        lines.append("\\ifdim\\dimen0>\\textheight \\dimen0=\\textheight\\fi")
        lines.append("\\advance\\dimen0 by -\\pagetotal")
        lines.append(f"\\advance\\dimen0 by -{reserve}")
        # A height of zero or less fails the compile, taking the booklet with it.
        lines.append("\\ifdim\\dimen0<12pt \\dimen0=12pt\\fi")
        # Never beyond the figure's own size: keepaspectratio scales to fill the
        # box it is given, so an image smaller than the measure would otherwise
        # be enlarged past its resolution.
        lines.append("\\dimen2=\\dimexpr\\ht0+\\dp0\\relax")
        lines.append("\\ifdim\\dimen0>\\dimen2 \\dimen0=\\dimen2\\fi")
        lines.append(f"\\dimen4={FIGURE_MAX_WIDTH}")
        lines.append("\\ifdim\\dimen4>\\wd0 \\dimen4=\\wd0\\fi")
        # Frozen to literals: \includegraphics uses the scratch registers too.
        lines.append("\\edef\\abstractfigureheight{\\the\\dimen0}")
        lines.append("\\edef\\abstractfigurewidth{\\the\\dimen4}")
        lines.append(
            "\\centerline{\\includegraphics["
            "width=\\abstractfigurewidth,"
            "height=\\abstractfigureheight,"
            "keepaspectratio"
            f"]{{{folder}/figure{out}}}}}")
        lines.append("\\endgroup")

    refs = abstract.resolved_references
    if refs:
        lines.append("")
        # Set as its own block rather than a list: an enumerate inherits the
        # body's line spacing and paragraph skip and reserves a wide label
        # margin, none of which suits a reference. \setstretch is local to the
        # group, so nothing here reaches the body text.
        lines.append(f"\\begingroup\\setstretch{{1}}{REF_FONT_SIZE}"
                     f"\\setlength{{\\parskip}}{{{REF_ITEM_SPACE}}}"
                     f"\\setlength{{\\parindent}}{{0pt}}")
        lines.append(f"\\vspace{{{REF_TOP_SPACE}}}")
        lines.append("\\noindent\\textbf{References}\\par")
        for ref in refs:
            meta = fetch_metadata(ref["doi"])
            if meta:
                cite = _latex_escape(format_reference_compact(meta))
            else:
                cite = ref["doi"].replace("_", "\\_")
            doi_esc = ref["doi"].replace("_", "\\_")
            # [n] matches how the body cites them; an enumerate prints "n.".
            lines.append(
                f"\\noindent\\hangindent=1.8em\\hangafter=1 [{ref['key']}]~"
                f"\\href{{https://doi.org/{doi_esc}}}{{{cite}}}\\par")
        lines.append("\\endgroup")

    lines.append("")
    lines.append("\\newpage")
    return "\n".join(lines)


def parse_authors(raw: str, presenting_idx: int = 0) -> tuple[str, str]:
    """Parse pipe-delimited author rows into LaTeX-formatted lines.

    Returns ``(author_line, affil_line)``.  Author names carry
    ``\\textsuperscript{…}`` affiliation markers.  The presenting author
    (by index) is wrapped in ``\\underline{…}``.
    """
    if not raw or not raw.strip():
        return ("", "")

    authors: list[tuple[str, str, str]] = []
    affil_map: dict[str, str] = {}
    seen_affils: set[str] = set()

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        name = parts[0].strip() if len(parts) > 0 else ""
        idx = parts[1].strip() if len(parts) > 1 else ""
        affil = parts[2].strip() if len(parts) > 2 else ""
        if name:
            authors.append((name, idx, affil))
            if idx and affil and affil not in seen_affils:
                seen_affils.add(affil)
                affil_map[idx] = affil

    if not authors:
        return ("", "")

    author_names: list[str] = []
    for i, (name, idx, _affil) in enumerate(authors):
        name_esc = _latex_escape(name)
        if idx:
            tag = f"{name_esc}\\textsuperscript{{{idx}}}"
        else:
            tag = name_esc
        if i == presenting_idx:
            tag = f"\\underline{{{tag}}}"
        author_names.append(tag)
    author_line = ", ".join(author_names)

    affil_parts: list[str] = []
    for idx in sorted(affil_map.keys(), key=int):
        affil_esc = _latex_escape(affil_map[idx])
        affil_parts.append(f"\\textsuperscript{{{idx}}}{affil_esc}")
    affil_line = "\\quad ".join(affil_parts)

    return (author_line, affil_line)


# ---------------------------------------------------------------------------
# One abstract, on its own
# ---------------------------------------------------------------------------

# A single page compiles in about a second; this ceiling is for a pathological
# figure, not a normal one.
_SINGLE_COMPILE_TIMEOUT = 90
_SINGLE_WAIT_TIMEOUT = 150


def render_abstract_pdf(abstract, *, uploads_root: Path) -> bytes:
    """One abstract as a PDF, laid out exactly as it will appear in the booklet.

    Deliberately the same fragment builder and preamble the booklet uses, so
    what a presenter downloads to check their submission is what the booklet
    will print — a preview that renders differently is worse than none.

    The conference's booklet header, footer and background are applied when
    they are set, for the same reason. Raises RenderError if the compile
    fails; callers decide whether that is fatal.
    """
    import shutil
    import tempfile

    from .documents import compile_prepared_dir

    conference = abstract.conference
    job = Path(tempfile.mkdtemp(prefix="abstract-"))
    try:
        def _copy(col_name: str, label: str) -> str | None:
            filename = getattr(conference, col_name, None) if conference else None
            if not filename:
                return None
            src = uploads_root / "conferences" / filename
            if not src.exists():
                return None
            return convert_for_latex(src, job / f"{label}{src.suffix}").name

        header_rel = _copy("booklet_header_filename", "header")
        footer_rel = _copy("booklet_footer_filename", "footer")
        bg_rel = _copy("booklet_background_filename", "background")

        label = "001"
        sub = job / f"abstract_{label}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"abstract_{label}.tex").write_text(
            abstract_fragment(label, abstract,
                              has_header=header_rel is not None,
                              has_background=bg_rel is not None),
            encoding="utf-8")

        for attr, name in (("figure_filename", "figure"),
                           ("profile_picture_filename", "profile")):
            filename = getattr(abstract, attr, None)
            if not filename:
                continue
            src = uploads_root / "abstracts" / filename.split("/", 1)[-1]
            if src.exists():
                convert_for_latex(src, sub / f"{name}{src.suffix}")

        # No table of contents and no title page: this is the abstract, not a
        # one-entry booklet.
        tex = booklet_preamble(
            conference, [f"\\input{{abstract_{label}/abstract_{label}.tex}}"],
            header_rel, footer_rel, bg_rel)
        tex = tex.replace("\\maketitle\n", "").replace("\\tableofcontents\n", "")
        tex = tex.replace("\\newpage\n\n\\input", "\\input")
        tex_path = job / "abstract.tex"
        tex_path.write_text(tex, encoding="utf-8")

        return compile_prepared_dir(job, tex_path,
                                    compile_timeout=_SINGLE_COMPILE_TIMEOUT,
                                    wait_timeout=_SINGLE_WAIT_TIMEOUT)
    finally:
        shutil.rmtree(job, ignore_errors=True)


def abstract_pdf_filename(abstract) -> str:
    """A download name a presenter can recognise among their files."""
    import re

    name = (abstract.presenting_author[0] or "").strip() or "abstract"
    title = (abstract.title or "").strip()
    stem = f"{name}-{title}"[:60]
    stem = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower() or "abstract"
    return f"{stem}.pdf"


def send_abstract_receipt(abstract, *, uploads_root: Path,
                          revision: bool = False) -> bool:
    """Confirm a submission in writing, with the abstract attached as a PDF.

    The submission page has always told authors their abstract was received
    and then sent nothing, so this closes that gap. The PDF is what makes it
    useful rather than merely polite: it is the same rendering the booklet
    will print, so an author can see how their formatting resolved while
    there is still time to fix it.

    A failed render never costs the email — the confirmation is the point and
    the attachment is the bonus, the same rule the document renderer applies
    to receipts. Returns whether the mail was accepted.
    """
    import logging

    from .mail import send_mail

    log = logging.getLogger(__name__)
    conference = abstract.conference
    author = abstract.author
    if author is None or not author.email:
        return False

    attachments = []
    try:
        attachments.append((abstract_pdf_filename(abstract),
                            render_abstract_pdf(abstract,
                                                uploads_root=uploads_root),
                            "application/pdf"))
        note = ("A PDF of your abstract is attached. It shows roughly how an "
                "abstract is typeset for the booklet, so you can check that "
                "your text, figure and references have come through as you "
                "intended. It is a guide to composition only — the final "
                "layout may differ, and it does not indicate that your "
                "abstract has been accepted.\n\n"
                "If anything is wrong, you can edit your submission until "
                "submissions close and we will use your latest version.\n\n")
    except Exception:
        log.exception("Abstract receipt PDF failed for abstract %s", abstract.id)
        note = ("You can review your submission, and edit it until "
                "submissions close, by logging in to your dashboard.\n\n")

    presenting = abstract.presenting_author[0] or author.full_name or ""
    opening = ("Thank you — we have received your updated abstract for"
               if revision else
               "Thank you — we have received your abstract for")
    body = (
        f"{opening} {conference.title} ({conference.date_range}).\n\n"
        f"Title: {abstract.title}\n"
        f"Presenting author: {presenting}\n\n"
        f"{note}"
        f"This is a receipt for your submission, not a decision. You will be "
        f"notified separately once abstracts have been reviewed.\n"
    )
    return send_mail(
        to=author.email,
        subject=(f"Abstract updated - {conference.title}" if revision
                 else f"Abstract received - {conference.title}"),
        body=body,
        attachments=attachments or None,
    )
