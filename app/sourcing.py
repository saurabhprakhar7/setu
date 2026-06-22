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

# Common Indian IT employers — used for the company filter + quick searches.
COMPANIES = ["TCS", "Infosys", "Wipro", "Accenture", "Cognizant", "HCL", "Tech Mahindra", "Capgemini"]

# One-click role/segment combos.
_ROLE_PRESETS = [
    ("Backend · open to work", {"role": "Backend Engineer", "segment": "active"}),
    ("Frontend · open to work", {"role": "Frontend Engineer", "segment": "active"}),
    ("Fullstack · open to work", {"role": "Fullstack Engineer", "segment": "active"}),
    ("Backend · freelance", {"role": "Backend Engineer", "segment": "freelance"}),
    ("Senior · open to work", {"role": "Engineer", "seniority": "Senior", "segment": "active"}),
]


def build_searches(role="", skills="", location="", seniority="", segment="", company="", min_years="") -> dict:
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]

    terms = []
    if role.strip():
        terms.append(f'"{role.strip()}"')
    if skill_list:
        terms.append("(" + " OR ".join(skill_list) + ")")
    if seniority.strip():
        terms.append(f'"{seniority.strip()}"')
    if company.strip():
        terms.append(f'"{company.strip()}"')
    if location.strip():
        terms.append(location.strip())
    if min_years:
        try:
            n = int(min_years)
            year_terms = " OR ".join(f'"{y} year' for y in range(n, n + 6))
            terms.append(f"({year_terms})")
        except ValueError:
            pass
    hook = _SEGMENT_HOOKS.get(segment, "")
    if hook:
        terms.append(hook)

    xray = " ".join(["site:linkedin.com/in", *terms, "-intitle:jobs"])
    keywords = " ".join(
        t for t in [role, " ".join(skill_list), seniority, company, location] if t.strip()
    )
    return {
        "xray": xray,
        "google_url": "https://www.google.com/search?q=" + urllib.parse.quote(xray),
        "linkedin_url": "https://www.linkedin.com/search/results/people/?keywords="
        + urllib.parse.quote(keywords),
    }


def role_presets() -> list[dict]:
    return [{"label": label, **build_searches(**filters)} for label, filters in _ROLE_PRESETS]


def company_presets() -> list[dict]:
    return [
        {"label": f"{c} · open to work", **build_searches(role="Engineer", company=c, segment="active")}
        for c in COMPANIES
    ]
