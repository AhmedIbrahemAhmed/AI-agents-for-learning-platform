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
    if not skills:
        return "\\textit{No skills available to display.}"
    lines = ["\\begin{itemize}"]
    for s in skills:
        pct = int(round(s.get("proficiency", 0.0) * 100))
        lines.append(f"  \\item {escape_latex(s.get('name', ''))} -- {pct}\\%")
    lines.append("\\end{itemize}")
    return "\n".join(lines)

def render_projects_latex(projects: List[Dict[str, Any]]) -> str:
    if not projects:
        return "\\textit{No projects available.}"
    lines = ["\\begin{itemize}"]
    for p in projects:
        title = escape_latex(p.get("title", ""))
        role = f" -- {escape_latex(p.get('role', ''))}" if p.get("role") else ""
        desc = f": {escape_latex(p.get('description', ''))}" if p.get("description") else ""
        
        sstr, estr = _format_date(p.get("start_date")), _format_date(p.get("end_date"))
        dates = f" ({sstr} -- {estr})" if (sstr or estr) else ""
        url = f" (\\url{{{escape_latex(p.get('url', ''))}}})" if p.get("url") else ""
        
        lines.append(f"  \\item \\textbf{{{title}}}{role}{desc}{dates}{url}")
    lines.append("\\end{itemize}")
    return "\n".join(lines)

def render_certificates_latex(certs: List[Dict[str, Any]]) -> str:
    if not certs:
        return "\\textit{No certifications available.}"
    lines = ["\\begin{itemize}"]
    for c in certs:
        name = escape_latex(c.get("name", ""))
        issuer = f" -- {escape_latex(c.get('issuer', ''))}" if c.get("issuer") else ""
        iden = f" (ID: {escape_latex(c.get('credential_id', ''))})" if c.get("credential_id") else ""
        url = f" (\\url{{{escape_latex(c.get('url', ''))}}})" if c.get("url") else ""
        
        lines.append(f"  \\item \\textbf{{{name}}}{issuer}{iden}{url}")
    lines.append("\\end{itemize}")
    return "\n".join(lines)

def render_educations_latex(edus: List[Dict[str, Any]]) -> str:
    if not edus:
        return "\\textit{No education records available.}"
    lines = ["\\begin{itemize}"]
    for e in edus:
        inst = escape_latex(e.get("institution", ""))
        deg = f", {escape_latex(e.get('degree', ''))}" if e.get("degree") else ""
        field = f" ({escape_latex(e.get('field', ''))})" if e.get("field") else ""
        dates = f" ({_format_date(e.get('start_date'))} -- {_format_date(e.get('end_date'))})" if (e.get('start_date') or e.get('end_date')) else ""
        loc = f" -- {escape_latex(e.get('location', ''))}" if e.get("location") else ""
        desc = f": {escape_latex(e.get('description', ''))}" if e.get("description") else ""
        # FIX: Added extra backslash to prevent \t interpretation as a tab character
        lines.append(f"  \\item \\textbf{{{inst}}}{deg}{field}{dates}{loc}{desc}")
    lines.append("\\end{itemize}")
    return "\n".join(lines)

def render_experiences_latex(exps: List[Dict[str, Any]]) -> str:
    if not exps:
        return "\\textit{No work experience available.}"
    lines = ["\\begin{itemize}"]
    for x in exps:
        comp = escape_latex(x.get("company", ""))
        role = f" -- {escape_latex(x.get('role', ''))}" if x.get("role") else ""
        dates = f" ({_format_date(x.get('start_date'))} -- {_format_date(x.get('end_date'))})" if (x.get('start_date') or x.get('end_date')) else ""
        loc = f" -- {escape_latex(x.get('location', ''))}" if x.get("location") else ""
        current = " [Current]" if x.get("current") else ""
        desc = f": {escape_latex(x.get('description', ''))}" if x.get("description") else ""
        # FIX: Added extra backslash to prevent \t interpretation as a tab character
        lines.append(f"  \\item \\textbf{{{comp}}}{role}{dates}{loc}{current}{desc}")
    lines.append("\\end{itemize}")
    return "\n".join(lines)

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
        return explicit_summary.strip()

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
            text = text.split("```")[-2].strip()
        text = text.replace('"', '').strip()
        
        if text:
            return text
    except Exception as e:
        # Silently catch API errors and log them, then move to algorithmic fallback
        sys.stderr.write(f"LLM Summary generation failed: {e}\n")

    # 3. Safe Hardcoded Fallback if API is down
    if parts:
        return "Experienced " + ", ".join(parts) + "."
    return ""

def generate_cv_latex(user_id: str, template_name: str = "simple_cv") -> Tuple[str, str]:
    tpl_path = os.path.join("templates", "cv", f"{template_name}.tex")
    if not os.path.exists(tpl_path):
        raise FileNotFoundError(f"Template not found: {tpl_path}")

    with open(tpl_path, "r", encoding="utf-8") as fh:
        tpl = fh.read()

    replacements = {
        "{{NAME}}": escape_latex(fetch_user_profile(user_id).get("name", "")),
        "{{EMAIL}}": escape_latex(fetch_user_profile(user_id).get("email", "")),
        "{{SKILLS}}": render_skills_latex(fetch_skills_from_view(user_id)),
        "{{PROJECTS}}": render_projects_latex(fetch_projects(user_id)),
        "{{CERTIFICATES}}": render_certificates_latex(fetch_certificates(user_id)),
        "{{EDUCATIONS}}": render_educations_latex(fetch_educations(user_id)),
        "{{EXPERIENCES}}": render_experiences_latex(fetch_experiences(user_id)),
        "{{SUMMARY}}": escape_latex(fetch_summary(user_id)),
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