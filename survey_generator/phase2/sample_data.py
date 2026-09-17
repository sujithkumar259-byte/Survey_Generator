"""
Phase 2 — hardcoded sample input (stands in for Phase 1 output).

A realistic HCP segmentation brief: approved hypotheses + study setup. The UI
lets the user edit these before running, so this is a starting point, not a
fixture.
"""
from .models import Hypothesis, StudySetup


SAMPLE_STUDY = StudySetup(
    study_type="Segmentation",
    target_population="Specialist physicians actively treating the target condition",
    disease_area="Immunology",
    frameworks=["Attitudes & behaviours", "Drivers & barriers", "Unmet need"],
    user_nuance=("Focus on prescribing drivers and adoption of newer biologics. "
                 "Client wants clean attitudinal items suitable for driver analysis."),
    client_name="Demo Client",
    study_title="HCP Segmentation — Immunology Biologics",
)


SAMPLE_HYPOTHESES = [
    Hypothesis(hypothesis_id="H001",
               text="Confidence in long-term safety data is the primary driver of biologic adoption.",
               category="Driver", target_segment="All", study_type="Segmentation"),
    Hypothesis(hypothesis_id="H002",
               text="Physicians who prioritise speed of onset are more likely to switch first-line therapy early.",
               category="Behaviour", target_segment="All", study_type="Segmentation"),
    Hypothesis(hypothesis_id="H003",
               text="Perceived administration burden is a key barrier to prescribing self-injectable biologics.",
               category="Barrier", target_segment="All", study_type="Segmentation"),
    Hypothesis(hypothesis_id="H004",
               text="Adherence support services materially influence brand choice among high-volume prescribers.",
               category="Driver", target_segment="High-volume", study_type="Segmentation"),
    Hypothesis(hypothesis_id="H005",
               text="Familiarity with emerging mechanisms of action correlates with earlier adoption.",
               category="Attitude", target_segment="All", study_type="Segmentation"),
    Hypothesis(hypothesis_id="H006",
               text="Cost and reimbursement concerns outweigh efficacy differences for a distinct conservative segment.",
               category="Barrier", target_segment="Conservative", study_type="Segmentation"),
    Hypothesis(hypothesis_id="H007",
               text="Peer and KOL influence is a stronger adoption driver than published trial data for early adopters.",
               category="Driver", target_segment="Early adopters", study_type="Segmentation"),
    Hypothesis(hypothesis_id="H008",
               text="Patient-reported quality-of-life outcomes are increasingly weighted in treatment selection.",
               category="Attitude", target_segment="All", study_type="Segmentation"),
]
