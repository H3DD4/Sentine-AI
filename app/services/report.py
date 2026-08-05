"""
Report generation service — fully async.
Generates branded DOCX reports with AI-drafted prose sections.
"""

import io
import asyncio
from docx import Document
from datetime import datetime
from app.models import Finding
from app.services.llm_client import AsyncLLMClient

SEVERITY_MAP = {
    "confirmed":     "Critical / High",
    "likely":        "Medium",
    "insufficient":  "Informational",
    "false_positive":"False Positive",
}


async def _llm_prose(prompt: str, max_tokens: int = 600) -> str:
    client = AsyncLLMClient()
    return await client.generate(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )


async def generate_report_docx(
    findings: list[Finding],
    engagement_title: str,
    client_name: str,
) -> bytes:
    """
    Generates a branded DOCX report.
    Data fields (CVE IDs, CVSS, verdicts) are inserted programmatically.
    Prose sections (exec summary, recommendations) are drafted by the LLM.
    Returns raw bytes of the .docx file.
    """
    doc = Document()

    # Title page
    doc.add_heading(f"{client_name} — Red Team Findings Report", 0)
    doc.add_paragraph(f"Engagement: {engagement_title}")
    doc.add_paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    doc.add_paragraph(f"Total findings: {len(findings)}")
    doc.add_paragraph("CONFIDENTIAL — TLP:RED — For authorised recipients only.")
    doc.add_page_break()

    # Executive summary — LLM drafted (run in parallel with per-finding prose)
    confirmed = sum(1 for f in findings if str(f.verdict) == "confirmed")
    likely = sum(1 for f in findings if str(f.verdict) == "likely")
    fp = sum(1 for f in findings if str(f.verdict) == "false_positive")

    summary_prompt = (
        f"Write a 3-paragraph executive summary for a penetration testing report "
        f"for client '{client_name}', engagement '{engagement_title}'. "
        f"The team found {len(findings)} findings: "
        f"{confirmed} confirmed, {likely} likely, {fp} false positives. "
        f"Professional tone. No bullet points. No markdown."
    )

    # Build per-finding prose prompts
    finding_prompts = []
    for finding in findings:
        finding_prompts.append(
            f"Write a concise 2-sentence technical risk context paragraph for this "
            f"penetration testing finding:\nTitle: {finding.title}\n"
            f"Verdict: {finding.verdict}\nReasoning: {finding.reasoning or 'Not provided'}\n"
            f"Matched CVEs: {', '.join(finding.matched_cves) if finding.matched_cves else 'None'}\n"
            f"Keep it professional. No markdown."
        )

    # Run executive summary + all finding prose in parallel
    all_prompts = [summary_prompt] + finding_prompts
    results = await asyncio.gather(*[_llm_prose(p) for p in all_prompts])

    exec_summary = results[0]
    finding_prose = results[1:]

    # Assemble document
    doc.add_heading("Executive Summary", 1)
    doc.add_paragraph(exec_summary)
    doc.add_page_break()

    doc.add_heading("Validated Findings", 1)
    for i, (finding, prose) in enumerate(zip(findings, finding_prose), 1):
        doc.add_heading(f"{i}. {finding.title}", 2)

        # Data table — programmatically inserted, NOT LLM generated
        table = doc.add_table(rows=4, cols=2)
        table.style = "Table Grid"
        rows = table.rows
        rows[0].cells[0].text = "Verdict"
        rows[0].cells[1].text = str(finding.verdict) if finding.verdict else "Pending"
        rows[1].cells[0].text = "Severity"
        rows[1].cells[1].text = SEVERITY_MAP.get(str(finding.verdict or ""), "N/A")
        rows[2].cells[0].text = "Confidence"
        rows[2].cells[1].text = f"{int((finding.confidence or 0) * 100)}%"
        rows[3].cells[0].text = "Matched CVEs"
        rows[3].cells[1].text = ", ".join(finding.matched_cves) if finding.matched_cves else "None"

        doc.add_paragraph()
        doc.add_heading("Risk Context", 3)
        doc.add_paragraph(prose)

        doc.add_heading("Analysis", 3)
        doc.add_paragraph(finding.reasoning or "No reasoning recorded.")

        if finding.matched_techniques:
            doc.add_heading("MITRE ATT&CK Techniques", 3)
            doc.add_paragraph(", ".join(finding.matched_techniques))

        if finding.recommended_next_steps:
            doc.add_heading("Recommendations", 3)
            for step in finding.recommended_next_steps:
                doc.add_paragraph(f"• {step}")

        if finding.missing_evidence:
            doc.add_heading("Evidence Gaps", 3)
            for gap in finding.missing_evidence:
                doc.add_paragraph(f"• {gap}")

        doc.add_page_break()

    # Disclaimer
    doc.add_heading("Disclaimer", 1)
    doc.add_paragraph(
        "This report was generated for authorized penetration testing purposes only. "
        "All findings were identified during a sanctioned engagement. "
        "Reproduction or distribution outside the authorized scope is prohibited."
    )

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()