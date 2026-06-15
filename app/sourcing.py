"""Build LinkedIn/Google X-ray search strings from filters (compliant sourcing).

The app only *generates searches* — the recruiter clicks through, reviews public
profiles, and pastes the good ones into Quick Add. No scraping, no auto-anything.
"""

import urllib.parse

_SEGMENT_HOOKS = {
    "active": '("open to work" OR "available")',
    "freelance": '("freelance" OR "contract" OR "contractor")',
    "passive": "",
}


def build_searches(role="", skills="", location="", seniority="", segment="") -> dict:
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]

    terms = []
    if role.strip():
        terms.append(f'"{role.strip()}"')
    if skill_list:
        terms.append("(" + " OR ".join(skill_list) + ")")
    if seniority.strip():
        terms.append(f'"{seniority.strip()}"')
    if location.strip():
        terms.append(location.strip())
    hook = _SEGMENT_HOOKS.get(segment, "")
    if hook:
        terms.append(hook)

    xray = " ".join(["site:linkedin.com/in", *terms, "-intitle:jobs"])
    keywords = " ".join(
        t for t in [role, " ".join(skill_list), seniority, location] if t.strip()
    )
    return {
        "xray": xray,
        "google_url": "https://www.google.com/search?q=" + urllib.parse.quote(xray),
        "linkedin_url": "https://www.linkedin.com/search/results/people/?keywords="
        + urllib.parse.quote(keywords),
    }
