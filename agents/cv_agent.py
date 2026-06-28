import json
import os
import re
import sys
from typing import List, Dict, Any, Tuple
from contextlib import closing
from database_tools import (
    fetch_user_profile as db_fetch_user_profile,
    fetch_skills_from_view as db_fetch_skills_from_view,
    fetch_projects as db_fetch_projects,
    fetch_certificates as db_fetch_certificates,
    fetch_educations as db_fetch_educations,
    fetch_experiences as db_fetch_experiences,
    fetch_summary as db_fetch_summary,
)
from pydantic import SecretStr
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

def escape_latex(s: str) -> str:
    if not s:
        return ""
    # Single-pass replacement prevents double-escaping generated LaTeX commands
    replacements = {
        "\\": r"\textbackslash{}", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "&": r"\&", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    pattern = re.compile("|".join(re.escape(k) for k in replacements.keys()))
    return pattern.sub(lambda m: replacements[m.group(0)], s)

def _format_date(d: Any) -> str:
    return d.strftime('%Y-%m') if hasattr(d, 'strftime') else (str(d) if d else '')

def fetch_user_profile(user_id: str) -> Dict[str, str]:
    return db_fetch_user_profile(user_id)

def fetch_skills_from_view(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    return db_fetch_skills_from_view(user_id, limit)

def fetch_projects(user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    return db_fetch_projects(user_id, limit)

def fetch_certificates(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    return db_fetch_certificates(user_id, limit)

def fetch_educations(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    return db_fetch_educations(user_id, limit)

def fetch_experiences(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    return db_fetch_experiences(user_id, limit)

def render_skills_latex(skills: List[Dict[str, Any]]) -> str:
    """Two-column tag-style grid instead of a flat bullet list."""
    if not skills:
        return "\\textit{No skills available to display.}"
    
    # Split into two columns
    mid = (len(skills) + 1) // 2
    left_col  = skills[:mid]
    right_col = skills[mid:]

    lines = [
        "\\begin{tabular}{@{}p{0.47\\textwidth}p{0.47\\textwidth}@{}}",
    ]
    for i in range(mid):
        l = left_col[i]
        r = right_col[i] if i < len(right_col) else None

        l_pct  = int(round(l.get("proficiency", 0.0) * 100))
        l_name = escape_latex(l.get("name", ""))
        left_cell = f"\\textbullet\\enskip\\textbf{{{l_name}}} \\textcolor{{muted}}{{({l_pct}\\%)}}"

        if r:
            r_pct  = int(round(r.get("proficiency", 0.0) * 100))
            r_name = escape_latex(r.get("name", ""))
            right_cell = f"\\textbullet\\enskip\\textbf{{{r_name}}} \\textcolor{{muted}}{{({r_pct}\\%)}}"
        else:
            right_cell = ""

        sep = "\\\\" if i < mid - 1 else ""
        lines.append(f"  {left_cell} & {right_cell} {sep}")

    lines.append("\\end{tabular}")
    return "\n".join(lines)

def render_projects_latex(projects: List[Dict[str, Any]]) -> str:
    """
    Each entry renders as:

      Project Title (Role)                   Start – End
      Description
      URL (if present)
    """
    if not projects:
        return "\\textit{No projects available.}"

    blocks = []
    for p in projects:
        title   = escape_latex(p.get("title", ""))
        role    = escape_latex(p.get("role", ""))
        desc    = escape_latex(p.get("description", ""))
        url     = p.get("url", "")
        sstr    = _format_date(p.get("start_date"))
        estr    = _format_date(p.get("end_date"))
        dates   = f"{sstr} -- {estr}" if (sstr or estr) else ""

        title_cell = f"\\textbf{{{title}}}"
        if role:
            title_cell += f" \\textcolor{{muted}}{{({role})}}"

        block = (
            f"\\noindent\\begin{{minipage}}{{\\textwidth}}\n"
            f"  \\begin{{tabularx}}{{\\textwidth}}{{@{{}}X r@{{}}}}\n"
            f"    {title_cell} & \\textcolor{{muted}}{{\\small {dates}}} \\\\\n"
            f"  \\end{{tabularx}}\n"
        )
        if desc:
            block += f"  \\vspace{{2pt}}\\small {desc}\\\\\n"
        if url:
            block += f"  \\vspace{{2pt}}\\textcolor{{accent}}{{\\small\\url{{{escape_latex(url)}}}}}\n"
        block += "\\end{minipage}\\vspace{6pt}"
        blocks.append(block)

    return "\n\n".join(blocks)


def render_certificates_latex(certs: List[Dict[str, Any]]) -> str:
    """
    Each entry renders as:

      Certificate Name                       Issuer
      Credential ID · URL
    """
    if not certs:
        return "\\textit{No certifications available.}"

    blocks = []
    for c in certs:
        name    = escape_latex(c.get("name", ""))
        issuer  = escape_latex(c.get("issuer", ""))
        cred_id = escape_latex(c.get("credential_id", ""))
        url     = c.get("url", "")

        block = (
            f"\\noindent\\begin{{minipage}}{{\\textwidth}}\n"
            f"  \\begin{{tabularx}}{{\\textwidth}}{{@{{}}X r@{{}}}}\n"
            f"    \\textbf{{{name}}} & \\textcolor{{muted}}{{\\small {issuer}}} \\\\\n"
            f"  \\end{{tabularx}}\n"
        )
        meta = []
        if cred_id:
            meta.append(f"ID: {cred_id}")
        if url:
            meta.append(f"\\textcolor{{accent}}{{\\url{{{escape_latex(url)}}}}}")
        if meta:
            block += f"  \\vspace{{2pt}}\\small\\textcolor{{muted}}{{{'  $\\cdot$  '.join(meta)}}}\n"
        block += "\\end{minipage}\\vspace{6pt}"
        blocks.append(block)

    return "\n\n".join(blocks)


def render_educations_latex(edus: List[Dict[str, Any]]) -> str:
    """
    Each entry renders as:

      Degree in Field                        Start – End
      Institution · Location
        Description
    """
    if not edus:
        return "\\textit{No education records available.}"

    blocks = []
    for e in edus:
        inst    = escape_latex(e.get("institution", ""))
        deg     = escape_latex(e.get("degree", ""))
        field   = escape_latex(e.get("field", ""))
        loc     = escape_latex(e.get("location", ""))
        desc    = escape_latex(e.get("description", ""))
        sstr    = _format_date(e.get("start_date"))
        estr    = _format_date(e.get("end_date"))
        dates   = f"{sstr} -- {estr}" if (sstr or estr) else ""

        # Degree line
        deg_line = ""
        if deg and field:
            deg_line = f"\\textbf{{{deg}}} \\textcolor{{muted}}{{in}} \\textbf{{{field}}}"
        elif deg:
            deg_line = f"\\textbf{{{deg}}}"
        elif field:
            deg_line = f"\\textbf{{{field}}}"
        else:
            deg_line = f"\\textbf{{{inst}}}"

        # Institution + location line
        inst_line = ""
        if inst and loc:
            inst_line = f"\\textcolor{{muted}}{{\\small {inst} $\\cdot$ {loc}}}"
        elif inst:
            inst_line = f"\\textcolor{{muted}}{{\\small {inst}}}"

        block = (
            f"\\noindent\\begin{{minipage}}{{\\textwidth}}\n"
            f"  \\begin{{tabularx}}{{\\textwidth}}{{@{{}}X r@{{}}}}\n"
            f"    {deg_line} & \\textcolor{{muted}}{{\\small {dates}}} \\\\\n"
        )
        if inst_line:
            block += f"    {inst_line} & \\\\\n"
        block += "  \\end{tabularx}\n"
        if desc:
            block += f"  \\vspace{{2pt}}\\small {desc}\n"
        block += "\\end{minipage}\\vspace{6pt}"
        blocks.append(block)

    return "\n\n".join(blocks)

def render_experiences_latex(exps: List[Dict[str, Any]]) -> str:
    """
    Each entry renders as:

      Role, Company                          Start – End (or Present)
      Location
        Description text
    """
    if not exps:
        return "\\textit{No work experience available.}"

    blocks = []
    for x in exps:
        role    = escape_latex(x.get("role", ""))
        comp    = escape_latex(x.get("company", ""))
        loc     = escape_latex(x.get("location", ""))
        desc    = escape_latex(x.get("description", ""))
        sstr    = _format_date(x.get("start_date"))
        estr    = "Present" if x.get("current") else _format_date(x.get("end_date"))
        dates   = f"{sstr} -- {estr}" if (sstr or estr) else ""

        # Line 1: Role — Company (right-aligned dates)
        heading = []
        if role and comp:
            heading.append(f"\\textbf{{{role}}} \\textcolor{{muted}}{{at}} \\textbf{{{comp}}}")
        elif role:
            heading.append(f"\\textbf{{{role}}}")
        elif comp:
            heading.append(f"\\textbf{{{comp}}}")

        line1 = (
            f"\\noindent\\begin{{minipage}}{{\\textwidth}}\n"
            f"  \\begin{{tabularx}}{{\\textwidth}}{{@{{}}X r@{{}}}}\n"
            f"    {' '.join(heading)} & \\textcolor{{muted}}{{\\small {dates}}} \\\\\n"
        )

        # Line 2: Location (if present)
        if loc:
            line1 += f"    \\textcolor{{muted}}{{\\small {loc}}} & \\\\\n"

        line1 += "  \\end{tabularx}\n"

        # Line 3: Description indented below
        if desc:
            line1 += f"  \\vspace{{2pt}}\\small {desc}\n"

        line1 += "\\end{minipage}\\vspace{6pt}"
        blocks.append(line1)

    return "\n\n".join(blocks)

def fetch_summary(
        user_id: str, 
        skills: list[dict[str, Any]] | None = None, 
        exps: list[dict[str, Any]] | None = None, 
        projects: list[dict[str, Any]] | None = None
    ) -> str:
    """
    Fetches the explicit profile summary. If empty, uses Groq LLM to generate 
    a highly professional resume summary using already fetched profile facts.
    """
    # 1. First, check if they have a pre-written summary in the DB
    explicit_summary = db_fetch_summary(user_id)
    if explicit_summary and explicit_summary.strip():
        return escape_latex(explicit_summary.strip())

    # 2. Fallback: If no explicit summary, use structured facts for the LLM
    # Default to fetching if data wasn't passed down (preserves backwards compatibility)
    skills = skills or fetch_skills_from_view(user_id, limit=3)
    exps = exps or fetch_experiences(user_id, limit=5)
    projects = projects or fetch_projects(user_id, limit=3)

    top_skills = ", ".join(str(s["name"]) for s in skills if s.get("name")) if skills else ""
    
    recent = None
    if exps:
        for e in exps:
            if e.get("current"):
                recent = e
                break
        if not recent:
            recent = exps[0]

    parts = []
    if recent and (recent.get("role") or recent.get("company")):
        rrole = recent.get("role") or ""
        rcomp = recent.get("company") or ""
        parts.append(f"{rrole} at {rcomp}".strip())
    if top_skills:
        parts.append(f"skilled in {top_skills}")

    facts = []
    if parts:
        facts.append(" ".join(parts))
    if projects:
        proj_names = ", ".join(str(p["title"]) for p in projects if p.get("title"))
        if proj_names:
            facts.append(f"Notable projects: {proj_names}.")

    fact_block = " ".join(facts)
    if not fact_block:
        return "Ambitious professional seeking to leverage core skills in an impactful new role."

    # Robust prompt engineering to prevent LLM chit-chat or Markdown formatting leaks
    prompt = (
        "You are an expert executive resume writer. Write a concise, compelling professional summary "
        "comprising exactly 2-3 sentences for a CV based ONLY on the profile facts provided below.\n\n"
        f"Profile Facts: {fact_block}\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "- Do NOT use markdown bolding, italics, or lists.\n"
        "- Do NOT wrap the output in quotation marks.\n"
        "- Output ONLY the direct summary text. Absolutely no conversational filler or preamble."
    )

    try:
        llm = ChatGroq(
            model="llama-3.3-70b-versatile", 
            api_key=SecretStr(os.getenv("GROQ_API_KEY") or ""), 
            temperature=0.2  # Low temperature keeps it close to the provided facts
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        text = str(resp.content).strip()
        
        # Clean up any lingering code fences or formatting oddities
        if "```" in text:
            text = re.sub(r"```[a-z]*\n?", "", text).strip()
        text = text.replace('"', '').strip()
        
        if text:
            return escape_latex(text)
    except Exception as e:
        # Silently catch API errors and log them, then move to algorithmic fallback
        sys.stderr.write(f"LLM Summary generation failed: {e}\n")

    # 3. Safe Hardcoded Fallback if API is down
    if parts:
        return escape_latex("Experienced " + ", ".join(parts) + ".")
    return ""

def generate_cv_latex(user_id: str, template_name: str = "simple_cv") -> Tuple[str, str]:
    tpl_path = os.path.join("templates", "cv", f"{template_name}.tex")
    if not os.path.exists(tpl_path):
        raise FileNotFoundError(f"Template not found: {tpl_path}")

    with open(tpl_path, "r", encoding="utf-8") as fh:
        tpl = fh.read()

    profile  = fetch_user_profile(user_id)
    skills   = fetch_skills_from_view(user_id)
    exps     = fetch_experiences(user_id)
    projects = fetch_projects(user_id)

    # Build social links conditionally — avoids orphan bullets when fields are empty
    linkedin = profile.get("linkedin", "")
    github   = profile.get("github", "")
    social_parts = []
    if linkedin:
        social_parts.append(
            f"\\href{{https://linkedin.com/in/{escape_latex(linkedin)}}}"
            f"{{\\textcolor{{accent}}{{linkedin.com/in/{escape_latex(linkedin)}}}}}"
        )
    if github:
        social_parts.append(
            f"\\href{{https://github.com/{escape_latex(github)}}}"
            f"{{\\textcolor{{accent}}{{github.com/{escape_latex(github)}}}}}"
        )
    social_links = (
        "\\textcolor{muted}{\\small "
        + "\\quad\\textbullet\\quad".join(social_parts)
        + "}"
    ) if social_parts else ""

    replacements = {
        "{{NAME}}":         escape_latex(profile.get("name", "")),
        "{{EMAIL}}":        escape_latex(profile.get("email", "")),
        "{{MOBILE}}":       escape_latex(profile.get("mobile", "")),
        "{{LOCATION}}":     escape_latex(profile.get("location", "")),
        "{{SOCIAL_LINKS}}": social_links,  # no escape_latex — already valid LaTeX
        "{{SKILLS}}":       render_skills_latex(skills),
        "{{PROJECTS}}":     render_projects_latex(projects),
        "{{CERTIFICATES}}": render_certificates_latex(fetch_certificates(user_id)),
        "{{EDUCATIONS}}":   render_educations_latex(fetch_educations(user_id)),
        "{{EXPERIENCES}}":  render_experiences_latex(exps),
        "{{SUMMARY}}":      fetch_summary(user_id, skills=skills, exps=exps, projects=projects),
    }

    rendered = tpl
    for placeholder, val in replacements.items():
        rendered = rendered.replace(placeholder, val)

    out_dir = os.path.join("outputs", "cv")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"cv_{user_id}.tex")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(rendered)

    return out_path, rendered

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agents/cv_agent.py <user_id>")
    else:
        path, raw_latex = generate_cv_latex(sys.argv[1])
        print(json.dumps({"tex_path": path, "latex": raw_latex}, indent=4))