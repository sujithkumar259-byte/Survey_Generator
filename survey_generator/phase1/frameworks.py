"""Phase 1 framework library and prompt helpers.

The UI lets analysts choose framework *blocks*. The hypothesis CSV `category`
column should then capture a specific construct/dimension from the selected
frameworks (for example Potential, Attitudes, Access barriers), not the framework
name itself.
"""
from __future__ import annotations

from typing import Iterable


FRAMEWORK_LIBRARY: dict[str, list[dict[str, object]]] = {
    "HCP Frameworks": [
        {
            "id": "hcp_pace_b",
            "name": "PACE-B Framework",
            "display_name": "HCP · PACE-B Framework",
            "audience": "HCPs",
            "categories": ["Potential", "Attitudes", "Capabilities", "Environment", "Behavior"],
            "summary": "Potential, Attitudes, Capabilities, Environment, and Behavior drivers/blockers of HCP importance and action.",
            "description": (
                "PACE-B identifies the major factors that explain why an HCP matters and what may drive or block behavior. "
                "Potential covers patient volume, eligible patient pool, prescribing opportunity, growth potential, expertise, and influence. "
                "Attitudes covers beliefs about disease, unmet need, treatment value, safety, efficacy, innovation, evidence, guidelines, and patient benefit. "
                "Capabilities covers knowledge, confidence, skills, diagnostic ability, patient identification ability, and operational know-how. "
                "Environment covers practice setting, account affiliation, payer restrictions, formulary access, protocols, peer norms, staffing, workflow, and referral networks. "
                "Behavior covers what HCPs actually do: diagnose, test, prescribe, refer, escalate, switch, monitor, educate, and engage."
            ),
        },
        {
            "id": "hcp_clinical_journey_adoption",
            "name": "Clinical Journey, Treatment Decision, and Adoption Framework",
            "display_name": "HCP · Clinical Journey, Treatment Decision, and Adoption Framework",
            "audience": "HCPs",
            "categories": [
                "Symptom recognition", "Diagnosis", "Testing", "Referral", "Treatment initiation",
                "Product selection", "Monitoring", "Escalation", "Switching", "Adoption readiness",
                "Guideline alignment", "Peer validation",
            ],
            "summary": "How HCPs make decisions across the care journey and move from awareness to routine adoption.",
            "description": (
                "This framework identifies how HCPs make decisions across the patient care journey and how they adopt new clinical behaviors. "
                "It covers symptom recognition, disease suspicion, diagnosis, testing, referral, treatment initiation, product or class selection, monitoring, adherence support, escalation, switching, discontinuation, progression, and long-term care. "
                "It also covers adoption readiness: unaware, aware, interested, considering, trialing, repeating, embedding into routine practice, advocating, or rejecting. "
                "Important variables include diagnostic threshold, testing frequency, patient selection logic, treatment trigger, guideline alignment, first-line preference, escalation threshold, switching logic, comfort with new mechanisms, need for peer validation, and trial-to-repeat behavior."
            ),
        },
        {
            "id": "hcp_mindset_influence_enablement",
            "name": "HCP Mindset, Influence, and Enablement Framework",
            "display_name": "HCP · Mindset, Influence, and Enablement Framework",
            "audience": "HCPs",
            "categories": [
                "Treatment philosophy", "Risk tolerance", "Evidence preference", "Innovation openness",
                "Patient-centricity", "Confidence", "Peer influence", "Local opinion leadership",
                "Referral influence", "Education needs", "Service needs", "Engagement preference",
                "Practical enablement",
            ],
            "summary": "Deeper HCP mindset, influence role, and enablement needs that shape receptivity and behavior change.",
            "description": (
                "This framework identifies the deeper clinical mindset of the HCP, their influence on others, and what would enable them to act differently. "
                "It covers treatment philosophy, risk tolerance, evidence preference, innovation openness, patient-centricity, confidence, peer influence, local opinion leadership, referral influence, committee influence, education needs, service needs, and engagement preferences. "
                "Important variables include safety-versus-efficacy orientation, early intervention belief, reliance on clinical trials versus real-world evidence, trust in guidelines, openness to new therapies, willingness to switch stable patients, KOL/local influencer role, congress engagement, rep access, MSL preference, digital engagement, patient identification tools, reimbursement support, and workflow support."
            ),
        },
    ],
    "Patient Frameworks": [
        {
            "id": "patient_burden_unmet_care_behavior",
            "name": "Disease Burden, Unmet Need, and Care Behavior Framework",
            "display_name": "Patient · Disease Burden, Unmet Need, and Care Behavior Framework",
            "audience": "Patients",
            "categories": [
                "Symptom burden", "Severity", "Progression risk", "Functional impact", "Emotional burden",
                "Quality of life", "Stigma", "Comorbidities", "Treatment satisfaction", "Care-seeking",
                "Adherence", "Persistence", "Switching", "Discontinuation", "Self-monitoring",
            ],
            "summary": "Patient disease experience, unmet need, treatment satisfaction, and actual care behavior.",
            "description": (
                "This framework identifies what matters about the patient's disease experience, level of unmet need, and actual behavior in care. "
                "It covers symptom burden, severity, acuity, progression risk, functional impact, emotional burden, quality of life, stigma, comorbidities, daily disruption, work impact, family impact, treatment satisfaction, and current care behavior. "
                "It also includes whether patients seek care, fill prescriptions, adhere, persist, switch, discontinue, self-monitor, use support programs, attend follow-ups, or abandon treatment."
            ),
        },
        {
            "id": "patient_journey",
            "name": "Patient Journey Framework",
            "display_name": "Patient · Journey Framework",
            "audience": "Patients",
            "categories": [
                "Symptom recognition", "Symptom normalization", "Care-seeking", "Diagnosis", "Specialist referral",
                "Treatment initiation", "Treatment adjustment", "Adherence", "Persistence", "Switching",
                "Relapse", "Progression", "Long-term management", "Support needs",
            ],
            "summary": "Where patients are in the disease and care pathway and what matters at each stage.",
            "description": (
                "The Patient Journey Framework identifies where the patient is in the disease and care pathway and what matters at each stage. "
                "It covers symptom recognition, symptom normalization, care-seeking, diagnosis, specialist referral, treatment initiation, treatment adjustment, adherence, persistence, switching, relapse, progression, long-term management, and ongoing support. "
                "Important variables include time from symptoms to care-seeking, time to diagnosis, number of HCPs seen before diagnosis, journey stage, treatment history, prior therapies, time on therapy, trigger for switching, side-effect experience, follow-up behavior, support received, and confidence about next steps."
            ),
        },
        {
            "id": "patient_mindset_situation_behavior",
            "name": "Mindset, Situation, and Behavior Framework",
            "display_name": "Patient · Mindset, Situation, and Behavior Framework",
            "audience": "Patients",
            "categories": [
                "Health beliefs", "Perceived seriousness", "Fear", "Denial", "Stigma", "Trust",
                "Treatment beliefs", "Self-efficacy", "Motivation", "Readiness to act", "Affordability",
                "Insurance", "Transportation", "Work constraints", "Caregiver support", "Health literacy",
                "Digital access", "Social support", "Care-seeking", "Adherence", "Information-seeking",
            ],
            "summary": "What patients think, what context they live in, and what they actually do.",
            "description": (
                "The Mindset, Situation, and Behavior Framework identifies what patients think, what context they live in, and what they actually do. "
                "Mindset includes health beliefs, perceived seriousness, fear, denial, stigma, trust in HCPs, trust in the healthcare system, treatment beliefs, self-efficacy, motivation, and readiness to act. "
                "Situation includes affordability, insurance, transportation, work constraints, family responsibilities, caregiver availability, health literacy, language, digital access, social support, and ability to navigate the system. "
                "Behavior includes care-seeking, treatment initiation, adherence, persistence, switching, discontinuation, information-seeking, HCP communication, and support program use."
            ),
        },
    ],
    "Account Frameworks": [
        {
            "id": "account_opportunity_strategic_value",
            "name": "Account Opportunity and Strategic Value Framework",
            "display_name": "Account · Opportunity and Strategic Value Framework",
            "audience": "Accounts",
            "categories": [
                "Patient volume", "Eligible patient pool", "Treatable opportunity", "Current class use",
                "Untreated opportunity", "Growth potential", "Market share opportunity", "Competitive intensity",
                "HCP footprint", "Referral catchment", "Academic role", "Research role", "Center of excellence", "Portfolio relevance",
            ],
            "summary": "Current, future, and strategic account value beyond size alone.",
            "description": (
                "This framework identifies how important an account is from a current, future, and strategic perspective. "
                "It covers patient volume, eligible patient pool, treatable population, current product or class use, untreated or undertreated opportunity, growth potential, market share opportunity, competitive intensity, number of relevant HCPs, number of sites, referral catchment, regional importance, academic role, research role, center-of-excellence status, and portfolio relevance."
            ),
        },
        {
            "id": "account_decision_power_governance_access",
            "name": "Decision Power, Governance, and Access Framework",
            "display_name": "Account · Decision Power, Governance, and Access Framework",
            "audience": "Accounts",
            "categories": [
                "Decision centralization", "Stakeholder complexity", "Formulary control", "Pathway control",
                "P&T influence", "Pharmacy influence", "Finance influence", "Procurement", "Site autonomy",
                "Contracting authority", "Payer mix", "Reimbursement pressure", "Prior authorization", "Denial rates", "Budget impact sensitivity",
            ],
            "summary": "How account decisions are made, who controls them, and how access is managed.",
            "description": (
                "This framework identifies how decisions are made inside the account, who controls them, and how access is managed. "
                "It covers centralized versus decentralized decision-making, stakeholder complexity, formulary control, pathway control, P&T committees, pharmacy influence, finance influence, procurement, medical leadership, administrative leadership, site autonomy, contracting authority, payer mix, reimbursement pressure, prior authorization burden, denial rates, step edits, specialty pharmacy rules, and budget impact sensitivity."
            ),
        },
        {
            "id": "account_care_delivery_implementation",
            "name": "Care Delivery, Operating Model, and Implementation Framework",
            "display_name": "Account · Care Delivery, Operating Model, and Implementation Framework",
            "audience": "Accounts",
            "categories": [
                "Treatment pathways", "Clinical protocols", "Order sets", "EHR prompts", "Referral rules",
                "Multidisciplinary teams", "Care coordinators", "Patient navigators", "Diagnostic infrastructure",
                "Administration capacity", "Specialty pharmacy integration", "Prior authorization support", "Staffing", "Workflow maturity", "Training capacity",
            ],
            "summary": "How care is organized and whether the account can operationalize change.",
            "description": (
                "This framework identifies how care is organized and whether the account can operationalize change. "
                "It covers treatment pathways, clinical protocols, order sets, EHR prompts, referral rules, multidisciplinary teams, care coordinators, patient navigators, site-of-care rules, diagnostic infrastructure, treatment administration capacity, specialty pharmacy integration, prior authorization support, staffing, workflow maturity, training capacity, and cross-site consistency."
            ),
        },
        {
            "id": "account_ecosystem_partnership",
            "name": "Ecosystem Role, Strategic Priorities, and Partnership Framework",
            "display_name": "Account · Ecosystem Role, Strategic Priorities, and Partnership Framework",
            "audience": "Accounts",
            "categories": [
                "Referral inflow", "Referral outflow", "Hub-and-spoke role", "Regional reach", "Teaching role",
                "Specialist density", "Trial participation", "Shared EHR networks", "Community influence",
                "Innovation readiness", "Leadership priorities", "Quality goals", "Population health goals",
                "Health equity goals", "Patient experience priorities", "Data maturity", "Digital maturity", "Partnership openness",
            ],
            "summary": "Broader market role and whether strategic priorities align for deeper engagement.",
            "description": (
                "This framework identifies the broader role an account plays in the market and whether there is strategic alignment for deeper engagement. "
                "It covers referral inflow, referral outflow, hub-and-spoke position, regional reach, teaching role, specialist density, trial participation, shared EHR networks, community influence, center-of-excellence status, innovation readiness, leadership priorities, quality goals, population health goals, health equity goals, patient experience priorities, data maturity, digital maturity, openness to pilots, prior partnerships, and willingness to co-create solutions."
            ),
        },
    ],
    "Consumer Frameworks": [
        {
            "id": "consumer_need_state_jtbd",
            "name": "Need State, Occasion, and Jobs-to-be-Done Framework",
            "display_name": "Consumer · Need State, Occasion, and Jobs-to-be-Done Framework",
            "audience": "Consumers",
            "categories": [
                "Functional needs", "Emotional needs", "Social needs", "Occasions", "Trigger events",
                "Urgency", "Desired benefit", "Current workaround", "Unmet need", "Decision criteria", "Trade-offs",
            ],
            "summary": "The problem consumers are trying to solve, the situation they are in, and the outcome they want.",
            "description": (
                "This framework identifies the problem the consumer is trying to solve, the situation they are in, and the outcome they want. "
                "It covers functional needs, emotional needs, social needs, occasions, trigger events, urgency, desired benefit, current workaround, unmet need, decision criteria, and trade-offs. "
                "Consumers may care about convenience, reassurance, control, confidence, speed, privacy, status, simplicity, safety, value, belonging, or self-expression."
            ),
        },
        {
            "id": "consumer_journey_readiness_barrier",
            "name": "Consumer Journey, Readiness, and Barrier Framework",
            "display_name": "Consumer · Journey, Readiness, and Barrier Framework",
            "audience": "Consumers",
            "categories": [
                "Awareness", "Need recognition", "Research", "Consideration", "Comparison", "Trial",
                "Purchase", "Repeat", "Loyalty", "Switching", "Advocacy", "Dormancy", "Reactivation",
                "Trust barrier", "Proof barrier", "Price concern", "Friction", "Choice overload", "Bad prior experience",
            ],
            "summary": "Where consumers are in the decision journey, how ready they are, and what blocks them.",
            "description": (
                "This framework identifies where the consumer is in the decision journey, how ready they are to act, and what prevents movement. "
                "It covers awareness, need recognition, research, consideration, comparison, trial, purchase, repeat, loyalty, switching, advocacy, dormancy, and reactivation. "
                "It also covers barriers such as low urgency, confusion, lack of trust, lack of proof, perceived risk, price concern, effort, friction, procrastination, choice overload, bad prior experience, and competing priorities."
            ),
        },
        {
            "id": "consumer_mindset_values_social",
            "name": "Mindset, Motivation, Values, and Social Context Framework",
            "display_name": "Consumer · Mindset, Motivation, Values, and Social Context Framework",
            "audience": "Consumers",
            "categories": [
                "Category beliefs", "Brand perceptions", "Trust", "Skepticism", "Confidence", "Anxiety",
                "Perceived risk", "Emotional reward", "Control", "Simplicity", "Expert validation", "Status orientation",
                "Wellness orientation", "Sustainability orientation", "Family influence", "Peer influence", "Influencer impact", "Community norms",
            ],
            "summary": "Beliefs, emotions, motivations, values, identity, and social context shaping choice.",
            "description": (
                "This framework identifies the beliefs, emotions, motivations, values, identity, and social context that shape consumer choices. "
                "It covers category beliefs, brand perceptions, trust, skepticism, confidence, anxiety, perceived risk, emotional reward, desire for control, desire for simplicity, expert validation, status orientation, wellness orientation, sustainability orientation, family orientation, privacy orientation, cultural values, peer influence, family influence, influencer impact, community norms, and lifestyle aspirations."
            ),
        },
        {
            "id": "consumer_behavior_value_loyalty_engagement",
            "name": "Behavior, Value, Loyalty, and Engagement Framework",
            "display_name": "Consumer · Behavior, Value, Loyalty, and Engagement Framework",
            "audience": "Consumers",
            "categories": [
                "Usage frequency", "Purchase frequency", "Spend", "Basket size", "Category involvement",
                "Share of wallet", "Repeat purchase", "Brand loyalty", "Switching behavior", "Promotion response",
                "Subscription behavior", "Churn risk", "Cross-sell potential", "Advocacy", "Price sensitivity", "Willingness to pay", "Channel preference", "Digital engagement",
            ],
            "summary": "What consumers do, how valuable they are, how loyal they are, and how they prefer to engage.",
            "description": (
                "This framework identifies what consumers actually do, how valuable they are, how loyal they are, and how they prefer to engage. "
                "It covers usage frequency, purchase frequency, spend, basket size, category involvement, share of wallet, repeat purchase, brand loyalty, switching behavior, promotion response, subscription behavior, churn risk, cross-sell potential, advocacy, reviews, referrals, complaints, returns, price sensitivity, willingness to pay, premium preference, discount responsiveness, channel preference, media behavior, digital engagement, app usage, email engagement, SMS receptivity, review reliance, expert reliance, and retail versus e-commerce preference."
            ),
        },
    ],
}

CUSTOM_FRAMEWORK_NAME = "Custom framework"


def flattened_frameworks() -> dict[str, str]:
    out: dict[str, str] = {}
    for group_items in FRAMEWORK_LIBRARY.values():
        for fw in group_items:
            out[str(fw["display_name"])] = str(fw["summary"])
    out[CUSTOM_FRAMEWORK_NAME] = "Analyst-defined framework prompt."
    return out


FRAMEWORKS = flattened_frameworks()


def framework_by_display_name(name: str) -> dict[str, object] | None:
    for group_items in FRAMEWORK_LIBRARY.values():
        for fw in group_items:
            if str(fw["display_name"]) == str(name):
                return fw
    return None


def selected_framework_prompt_block(selected: Iterable[str] | None, custom_prompt: str = "") -> str:
    """Return a prompt-ready block with full framework definitions."""
    selected_names = [str(s).strip() for s in selected or [] if str(s).strip()]
    blocks: list[str] = []
    for idx, name in enumerate(selected_names, start=1):
        if name == CUSTOM_FRAMEWORK_NAME:
            if custom_prompt.strip():
                blocks.append(
                    f"{idx}. {CUSTOM_FRAMEWORK_NAME}\n"
                    f"Audience: Analyst-defined\n"
                    f"Category guidance: Use the key constructs/dimensions explicitly named in the custom framework.\n"
                    f"Definition:\n{custom_prompt.strip()}"
                )
            continue
        fw = framework_by_display_name(name)
        if not fw:
            blocks.append(
                f"{idx}. {name}\n"
                "Category guidance: infer specific constructs/dimensions from this framework name and the study context."
            )
            continue
        blocks.append(
            f"{idx}. {fw['display_name']}\n"
            f"Audience: {fw['audience']}\n"
            f"Category guidance: {', '.join(str(c) for c in fw['categories'])}\n"
            f"Definition:\n{fw['description']}"
        )
    if not blocks:
        return "No framework selected. Infer practical categories from the study objective, audience, and uploaded source material."
    return "\n\n".join(blocks)


def category_suggestions(selected: Iterable[str] | None, custom_prompt: str = "") -> list[str]:
    """Return suggested category/construct names for examples and row recovery.

    This is guidance, not a strict validation list; users may add custom category
    text in the editor.
    """
    suggestions: list[str] = []
    for name in selected or []:
        fw = framework_by_display_name(str(name))
        if fw:
            for cat in fw.get("categories", []):
                cat_text = str(cat).strip()
                if cat_text and cat_text not in suggestions:
                    suggestions.append(cat_text)
    if CUSTOM_FRAMEWORK_NAME in [str(s).strip() for s in selected or []] and custom_prompt.strip():
        # Lightweight extraction of candidate category labels from custom text.
        for token in custom_prompt.replace(";", ",").split(","):
            token = " ".join(token.strip().split())
            if 2 <= len(token) <= 40 and token[0].isalpha() and token not in suggestions:
                suggestions.append(token)
            if len(suggestions) >= 20:
                break
    return suggestions


def selected_framework_names(selected: Iterable[str] | None) -> list[str]:
    return [str(s).strip() for s in selected or [] if str(s).strip()]
