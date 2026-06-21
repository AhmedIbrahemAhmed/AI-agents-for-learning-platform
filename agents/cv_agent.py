import json
import os
import re
import sys
from typing import List, Dict, Any, Tuple
from contextlib import closing
from database_tools import get_conn

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

def fetch_user_profile(user_id: int) -> Dict[str, str]:
    with closing(get_conn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT Name, Email FROM Users WHERE UserId = ?", user_id)
        row = cur.fetchone()
        return {"name": row[0] or "", "email": row[1] or ""} if row else {"name": "", "email": ""}

def fetch_skills_from_view(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT SkillName, Proficiency, Confidence FROM dbo.UserSkillsView WHERE UserId = ? ORDER BY Proficiency DESC, EvidenceCount DESC",
            user_id,
        )
        return [
            {"name": r[0], "proficiency": float(r[1] or 0.0), "confidence": float(r[2] or 0.0)}
            for r in cur.fetchall()[:limit]
        ]

def fetch_projects(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT Title, Description, Url, StartDate, EndDate, Role, Technologies FROM Projects WHERE UserId = ? ORDER BY COALESCE(StartDate, EndDate) DESC",
                user_id,
            )
            return [
                {"title": r[0] or "", "description": r[1] or "", "url": r[2] or "", "start_date": r[3], "end_date": r[4], "role": r[5] or "", "technologies": r[6] or ""}
                for r in cur.fetchall()[:limit]
            ]
        except Exception:
            cur.execute("SELECT Title FROM Projects WHERE UserId = ?", user_id)
            return [{"title": r[0] or "", "description": "", "url": ""} for r in cur.fetchall()[:limit]]

def fetch_certificates(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn, conn.cursor() as cur:
        try:
            cur.execute("SELECT Name, Issuer, IssueDate, CredentialId, Url, Description FROM Certificates WHERE UserId = ? ORDER BY IssueDate DESC", user_id)
            return [
                {"name": r[0] or "", "issuer": r[1] or "", "issue_date": r[2], "credential_id": r[3] or "", "url": r[4] or "", "description": r[5] or ""}
                for r in cur.fetchall()[:limit]
            ]
        except Exception:
            return []

def fetch_educations(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT Institution, Degree, Field, StartDate, EndDate, Location, Description, SortOrder FROM Educations WHERE UserId = ? ORDER BY COALESCE(SortOrder, 999)",
                user_id,
            )
            return [
                {
                    "institution": r[0] or "",
                    "degree": r[1] or "",
                    "field": r[2] or "",
                    "start_date": r[3],
                    "end_date": r[4],
                    "location": r[5] or "",
                    "description": r[6] or "",
                    "sort_order": r[7] if len(r) > 7 else None,
                }
                for r in cur.fetchall()[:limit]
            ]
        except Exception:
            return []

def fetch_experiences(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with closing(get_conn()) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT Company, Role, StartDate, EndDate, Location, Description, [Current], SortOrder FROM Experiences WHERE UserId = ? ORDER BY COALESCE(SortOrder, 999)",
                user_id,
            )
            return [
                {
                    "company": r[0] or "",
                    "role": r[1] or "",
                    "start_date": r[2],
                    "end_date": r[3],
                    "location": r[4] or "",
                    "description": r[5] or "",
                    "current": bool(r[6]) if r[6] is not None else False,
                    "sort_order": r[7] if len(r) > 7 else None,
                }
                for r in cur.fetchall()[:limit]
            ]
        except Exception:
            return []

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

def fetch_summary(user_id: int, max_skills: int = 3) -> str:
    candidate_fields = ["Summary", "Bio", "About", "ProfessionalSummary", "Description"]
    with closing(get_conn()) as conn, conn.cursor() as cur:
        for fld in candidate_fields:
            try:
                cur.execute(f"SELECT {fld} FROM Users WHERE UserId = ?", user_id)
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
            except Exception:
                continue

    skills = fetch_skills_from_view(user_id, limit=max_skills)
    exps = fetch_experiences(user_id, limit=5)
    top_skills = ", ".join(s.get("name") for s in skills[:max_skills]) if skills else ""
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

    if parts:
        return "Experienced " + ", ".join(parts) + "."
    return ""

def generate_cv_latex(user_id: int, template_name: str = "simple_cv") -> Tuple[str, str]:
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
        path, raw_latex = generate_cv_latex(int(sys.argv[1]))
        print(json.dumps({"tex_path": path, "latex": raw_latex}, indent=4))