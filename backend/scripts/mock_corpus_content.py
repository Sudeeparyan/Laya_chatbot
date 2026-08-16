"""Source text for the synthetic evaluation corpus.

Every scheme name, benefit and value in this file is invented. The documents
are shaped like the health-insurance benefit tables and claims procedures the
system was built against — same section structure, same vocabulary, same kind
of qualifier ("70% refund, 1 per surface every 2 years") — so retrieval and the
knowledge graph are exercised the way the real corpus exercises them, without
any of the host organisation's approved wording or approved figures.

The overlap between documents is deliberate and is the point of the set. A
term such as "pre-authorisation" or "waiting period" appears in six of the nine
documents; "physiotherapy" appears in three; the claims procedure references
vocabulary from every benefit schedule. Those shared mentions are what give the
knowledge graph its cross-document edges — without them the graph degenerates
into nine disconnected stars and the graph-expansion retriever has nothing to
expand along.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    """One heading and the body under it.

    ``table`` is a header row followed by data rows; it renders as a real Word
    table so the converter has to flatten it exactly as it flattens a live one.
    """

    heading: str
    paragraphs: list[str] = field(default_factory=list)
    table: list[list[str]] | None = None


@dataclass
class MockDocument:
    filename: str
    title: str
    department: str
    access_group: str
    classification: str
    version: str
    owner: str
    intro: str
    sections: list[Section]


# ---------------------------------------------------------------------------
# 1 & 2 — a two-tier dental pair.
#
# Written as a pair on purpose: the same treatment names carry different
# percentages and frequencies in each. A retriever that matches on terms alone
# will happily hand back the Essential row when asked about Complete, which is
# exactly the failure the evaluation set needs to be able to catch.
# ---------------------------------------------------------------------------

DENTAL_ESSENTIAL = MockDocument(
    filename="Corevita Dental Essential.docx",
    title="Corevita Dental Essential",
    department="Claims",
    access_group="KH_CLAIMS_USERS",
    classification="Internal",
    version="2026.1",
    owner="Dental Benefits Team",
    intro=(
        "Corevita Dental Essential is the entry tier of the Corevita dental range. "
        "This schedule sets out what the plan refunds, the frequency limits that apply "
        "to each treatment, and the conditions a claim must meet before it is paid. "
        "Effective 1 March 2026."
    ),
    sections=[
        Section(
            heading="Investigative and Preventative Treatment",
            paragraphs=[
                "Preventative treatment is covered from the start of cover with no waiting period. "
                "Refunds are paid against the member's annual dental limit of 620 euro."
            ],
            table=[
                ["Treatment", "Refund", "Frequency limit"],
                ["Routine examination", "100% refund", "2 per year"],
                ["Scaling and polishing", "80% refund", "1 per year"],
                ["Bitewing X-ray", "100% refund", "2 per year"],
                ["Panoramic X-ray", "100% refund", "1 every 3 years"],
                ["Periapical X-ray", "100% refund", "3 per year"],
                ["Fluoride treatment", "75% refund", "1 per year up to the age of 16"],
            ],
        ),
        Section(
            heading="Basic Restorative Treatment",
            paragraphs=[
                "Restorative treatment carries a waiting period of 6 months from the member's "
                "join date. Claims submitted inside the waiting period are declined and the "
                "member is notified in writing."
            ],
            table=[
                ["Treatment", "Refund", "Frequency limit"],
                ["Amalgam filling", "60% refund", "1 per surface every 2 years"],
                ["Composite filling", "60% refund", "1 per surface every 2 years"],
                ["Stainless steel crown", "50% refund", "1 every 5 years up to the age of 18"],
                ["Fissure sealant", "70% refund", "1 per tooth every 3 years"],
                ["Extraction, simple", "60% refund", "no annual limit"],
            ],
        ),
        Section(
            heading="Emergency Dental Treatment",
            paragraphs=[
                "Emergency treatment for the immediate relief of pain or infection is refunded at "
                "100% once per 12-month period, worldwide. The member should retain the treating "
                "dentist's receipt; direct settlement is not available outside the Republic of Ireland."
            ],
        ),
        Section(
            heading="What This Tier Does Not Cover",
            paragraphs=[
                "Orthodontic treatment is not covered under Essential and is available only on "
                "Corevita Dental Complete.",
                "Dental implants, veneers and cosmetic whitening are excluded on all Corevita tiers.",
                "Treatment started before the cover start date is excluded, even where the course "
                "of treatment completes after cover begins.",
            ],
        ),
        Section(
            heading="Claiming Under This Schedule",
            paragraphs=[
                "Claims must be submitted within 6 months of the treatment date. A claim submitted "
                "after that window is declined without assessment.",
                "Pre-authorisation is not required for any treatment on this schedule. "
                "See the Corevita Claims Handling Procedure for the assessment steps and the "
                "member appeal route.",
            ],
        ),
    ],
)


DENTAL_COMPLETE = MockDocument(
    filename="Corevita Dental Complete.docx",
    title="Corevita Dental Complete",
    department="Claims",
    access_group="KH_CLAIMS_USERS",
    classification="Internal",
    version="2026.1",
    owner="Dental Benefits Team",
    intro=(
        "Corevita Dental Complete is the upper tier of the Corevita dental range. It carries the "
        "Essential schedule in full at a higher refund rate and adds major restorative and "
        "orthodontic cover. Effective 1 March 2026."
    ),
    sections=[
        Section(
            heading="Investigative and Preventative Treatment",
            paragraphs=[
                "Preventative treatment is covered from the start of cover with no waiting period. "
                "Refunds are paid against the member's annual dental limit of 1,450 euro."
            ],
            table=[
                ["Treatment", "Refund", "Frequency limit"],
                ["Routine examination", "100% refund", "3 per year"],
                ["Scaling and polishing", "100% refund", "2 per year"],
                ["Bitewing X-ray", "100% refund", "3 per year"],
                ["Panoramic X-ray", "100% refund", "1 every 2 years"],
                ["Periapical X-ray", "100% refund", "5 per year"],
                ["Fluoride treatment", "100% refund", "2 per year up to the age of 18"],
            ],
        ),
        Section(
            heading="Basic Restorative Treatment",
            paragraphs=[
                "Restorative treatment carries a waiting period of 3 months from the member's "
                "join date."
            ],
            table=[
                ["Treatment", "Refund", "Frequency limit"],
                ["Amalgam filling", "80% refund", "1 per surface every 2 years"],
                ["Composite filling", "80% refund", "1 per surface every 2 years"],
                ["Stainless steel crown", "75% refund", "1 every 4 years up to the age of 18"],
                ["Fissure sealant", "90% refund", "1 per tooth every 2 years"],
                ["Extraction, simple", "80% refund", "no annual limit"],
            ],
        ),
        Section(
            heading="Major Restorative Treatment",
            paragraphs=[
                "Major restorative treatment carries a waiting period of 12 months and requires "
                "pre-authorisation before the course of treatment begins. Submit the treating "
                "dentist's treatment plan and estimate to the Dental Benefits Team."
            ],
            table=[
                ["Treatment", "Refund", "Frequency limit"],
                ["Root canal treatment, incisor", "70% refund", "1 per tooth every 5 years"],
                ["Root canal treatment, molar", "70% refund", "1 per tooth every 5 years"],
                ["Porcelain crown", "60% refund", "1 per tooth every 7 years"],
                ["Bridge, per unit", "60% refund", "1 every 7 years"],
                ["Denture, full upper or lower", "55% refund", "1 every 5 years"],
                ["Periodontal treatment", "65% refund", "2 courses per year"],
            ],
        ),
        Section(
            heading="Orthodontic Treatment",
            paragraphs=[
                "Orthodontic treatment is covered for members under the age of 18 at the start of "
                "the course of treatment, subject to a waiting period of 24 months and to "
                "pre-authorisation.",
                "The lifetime orthodontic limit is 2,200 euro per member. Refunds are paid at 50% "
                "of the treating orthodontist's fee and are released in instalments against the "
                "agreed treatment plan.",
            ],
        ),
        Section(
            heading="Emergency Dental Treatment",
            paragraphs=[
                "Emergency treatment for the immediate relief of pain or infection is refunded at "
                "100% twice per 12-month period, worldwide.",
            ],
        ),
        Section(
            heading="Exclusions",
            paragraphs=[
                "Dental implants, veneers and cosmetic whitening are excluded on all Corevita tiers.",
                "Orthodontic treatment begun before the 24-month waiting period expires is excluded "
                "in full, including the portion of the course delivered after the waiting period ends.",
            ],
        ),
        Section(
            heading="Claiming Under This Schedule",
            paragraphs=[
                "Claims must be submitted within 6 months of the treatment date.",
                "Pre-authorisation is required for major restorative and orthodontic treatment only. "
                "See the Corevita Claims Handling Procedure for the assessment steps.",
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# 3 & 4 — hospital cover and the day-case schedule it refers out to.
#
# Split across two documents on purpose. "What is the excess on a day case?"
# is answered by a sentence in one and a table row in the other, so it is a
# question single-passage retrieval gets half-right and graph expansion can
# join up.
# ---------------------------------------------------------------------------

HOSPITAL_COVER = MockDocument(
    filename="Meridian Hospital Cover.docx",
    title="Meridian Hospital Cover",
    department="Claims",
    access_group="KH_CLAIMS_USERS",
    classification="Internal",
    version="2026.2",
    owner="Hospital Benefits Team",
    intro=(
        "Meridian Hospital Cover sets out in-patient and day-case entitlements, the accommodation "
        "levels covered in each hospital category, and the excess that applies per admission. "
        "Effective 1 April 2026."
    ),
    sections=[
        Section(
            heading="Hospital Categories",
            paragraphs=[
                "Cover level depends on the category of the treating hospital. Category is set by "
                "the published directory and is reviewed each January."
            ],
            table=[
                ["Category", "Accommodation covered", "Cover level"],
                ["Public hospital", "Public ward, semi-private", "100% of agreed charges"],
                ["Participating private hospital", "Semi-private, private", "100% of agreed charges"],
                ["High-tech hospital", "Semi-private, private", "90% of agreed charges"],
                ["Non-participating hospital", "Semi-private only", "65% of agreed charges"],
            ],
        ),
        Section(
            heading="In-Patient Treatment",
            paragraphs=[
                "In-patient treatment requires a consultant referral and pre-authorisation for any "
                "planned admission. Emergency admissions are authorised retrospectively provided "
                "the member notifies the Hospital Benefits Team within 48 hours.",
                "An excess of 150 euro applies per in-patient admission. The excess is waived where "
                "the admission follows an accident and emergency attendance in the preceding 24 hours.",
            ],
            table=[
                ["Benefit", "Entitlement", "Condition"],
                ["Semi-private accommodation", "Full cover in participating hospitals", "Pre-authorisation required"],
                ["Private accommodation", "Full cover in participating hospitals", "Pre-authorisation required"],
                ["Consultant in-patient fees", "Full cover at agreed rates", "Consultant referral required"],
                ["Intensive care unit", "Full cover, no day limit", "None"],
                ["Prosthesis or appliance", "Up to 8,500 euro per admission", "Pre-authorisation required"],
            ],
        ),
        Section(
            heading="Day-Case Treatment",
            paragraphs=[
                "Day-case procedures are covered in full in participating hospitals. An excess of "
                "75 euro applies per day case. The procedure must appear on the Meridian Day-Case "
                "Schedule; procedures not listed are assessed individually by the Hospital Benefits Team.",
            ],
        ),
        Section(
            heading="Diagnostic Imaging",
            paragraphs=[
                "Scans arranged during an in-patient or day-case stay are covered as part of the "
                "admission and are not separately claimable.",
            ],
            table=[
                ["Scan type", "In-patient or day case", "Out-patient"],
                ["MRI scan", "Covered in full", "Up to 320 euro, 2 per year"],
                ["CT scan", "Covered in full", "Up to 260 euro, 2 per year"],
                ["Ultrasound scan", "Covered in full", "Up to 140 euro, 3 per year"],
                ["DEXA bone scan", "Covered in full", "Up to 130 euro, 1 every 2 years"],
            ],
        ),
        Section(
            heading="Waiting Periods",
            paragraphs=[
                "A waiting period of 26 weeks applies to all in-patient and day-case benefits for "
                "new members. A waiting period of 52 weeks applies to any pre-existing condition "
                "declared at the point of joining.",
                "Waiting periods are carried over in full where a member transfers from another "
                "insurer without a break in cover exceeding 13 weeks.",
            ],
        ),
        Section(
            heading="Exclusions",
            paragraphs=[
                "Cosmetic surgery is excluded unless it is reconstructive and follows an accident or "
                "cancer treatment covered under this plan.",
                "Treatment in a non-participating hospital without pre-authorisation is refunded at "
                "50% of the agreed charge for the equivalent participating hospital.",
            ],
        ),
    ],
)


DAY_CASE_SCHEDULE = MockDocument(
    filename="Meridian Day-Case Schedule.docx",
    title="Meridian Day-Case Schedule",
    department="Claims",
    access_group="KH_CLAIMS_USERS",
    classification="Internal",
    version="2026.2",
    owner="Hospital Benefits Team",
    intro=(
        "The listed day-case procedures covered under Meridian Hospital Cover, with the "
        "co-payment that applies to each and whether pre-authorisation is needed. "
        "Read alongside Meridian Hospital Cover. Effective 1 April 2026."
    ),
    sections=[
        Section(
            heading="How to Read This Schedule",
            paragraphs=[
                "Every procedure on this schedule is covered in a participating hospital subject to "
                "the standard day-case excess of 75 euro set out in Meridian Hospital Cover. Where a "
                "co-payment is listed below, it applies in addition to that excess.",
                "A procedure that does not appear on this schedule is not automatically excluded. "
                "It is assessed individually and the member is told the outcome within 10 working days.",
            ],
        ),
        Section(
            heading="Endoscopic Procedures",
            table=[
                ["Procedure", "Co-payment", "Pre-authorisation", "Frequency limit"],
                ["Gastroscopy", "None", "Not required", "2 per year"],
                ["Colonoscopy", "None", "Not required", "1 per year"],
                ["Flexible sigmoidoscopy", "None", "Not required", "2 per year"],
                ["Bronchoscopy", "50 euro", "Required", "1 per year"],
                ["Cystoscopy", "50 euro", "Not required", "2 per year"],
            ],
        ),
        Section(
            heading="Orthopaedic Procedures",
            table=[
                ["Procedure", "Co-payment", "Pre-authorisation", "Frequency limit"],
                ["Knee arthroscopy", "120 euro", "Required", "1 per joint per year"],
                ["Shoulder arthroscopy", "120 euro", "Required", "1 per joint per year"],
                ["Carpal tunnel release", "80 euro", "Required", "1 per hand"],
                ["Trigger finger release", "80 euro", "Not required", "2 per year"],
                ["Joint injection under imaging", "40 euro", "Not required", "3 per year"],
            ],
        ),
        Section(
            heading="Ophthalmic and ENT Procedures",
            table=[
                ["Procedure", "Co-payment", "Pre-authorisation", "Frequency limit"],
                ["Cataract removal, per eye", "200 euro", "Required", "1 per eye"],
                ["Grommet insertion", "60 euro", "Required", "2 per year up to the age of 16"],
                ["Tonsillectomy", "150 euro", "Required", "1 per lifetime"],
                ["Nasal septum correction", "180 euro", "Required", "1 per lifetime"],
            ],
        ),
        Section(
            heading="Procedures Excluded From Day-Case Cover",
            paragraphs=[
                "Any procedure requiring an overnight stay is assessed as an in-patient admission "
                "and carries the in-patient excess of 150 euro instead of the day-case excess.",
                "Procedures carried out in a consultant's rooms rather than a hospital day-case unit "
                "are claimable under Everyday Health Cashback as an out-patient consultation, not "
                "under this schedule.",
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# 5 — the out-patient plan. The hub of the corpus: it shares vocabulary with
# the dental pair, the hospital pair, maternity and mental wellbeing.
# ---------------------------------------------------------------------------

EVERYDAY_CASHBACK = MockDocument(
    filename="Everyday Health Cashback.docx",
    title="Everyday Health Cashback",
    department="Member Services",
    access_group="KH_MEMBER_SERVICES",
    classification="Internal",
    version="2026.3",
    owner="Everyday Benefits Team",
    intro=(
        "Everyday Health Cashback refunds routine out-patient costs that fall below the level at "
        "which hospital cover applies: GP visits, consultant consultations, therapies and "
        "prescription charges. Effective 1 February 2026."
    ),
    sections=[
        Section(
            heading="Annual Out-Patient Limit",
            paragraphs=[
                "All benefits on this schedule share a combined out-patient limit of 900 euro per "
                "member per year. An out-patient excess of 1 euro applies to the first claim in each "
                "membership year and is deducted automatically.",
                "The limit runs on the membership year, not the calendar year. Unused benefit does "
                "not carry forward.",
            ],
        ),
        Section(
            heading="Consultations",
            table=[
                ["Benefit", "Refund", "Frequency limit"],
                ["GP visit", "35 euro per visit", "8 per year"],
                ["Out-of-hours GP visit", "50 euro per visit", "4 per year"],
                ["Consultant consultation, initial", "90 euro per visit", "4 per year"],
                ["Consultant consultation, follow-up", "60 euro per visit", "6 per year"],
                ["Practice nurse visit", "20 euro per visit", "6 per year"],
            ],
        ),
        Section(
            heading="Therapies",
            paragraphs=[
                "Therapy benefits require a GP referral. The referral must be dated before the first "
                "treatment in the course. No pre-authorisation is needed."
            ],
            table=[
                ["Therapy", "Refund", "Frequency limit"],
                ["Physiotherapy", "45 euro per session", "10 per year"],
                ["Chiropractic treatment", "40 euro per session", "6 per year"],
                ["Osteopathy", "40 euro per session", "6 per year"],
                ["Dietetics", "45 euro per session", "4 per year"],
                ["Speech and language therapy", "50 euro per session", "8 per year"],
                ["Occupational therapy", "50 euro per session", "6 per year"],
            ],
        ),
        Section(
            heading="Diagnostics and Prescriptions",
            table=[
                ["Benefit", "Refund", "Frequency limit"],
                ["Out-patient MRI scan", "Up to 320 euro", "2 per year"],
                ["Blood tests ordered by a GP", "30 euro per test", "6 per year"],
                ["Prescription charges", "50% refund", "up to 250 euro per year"],
                ["Vaccination, non-routine travel", "40 euro per vaccination", "3 per year"],
            ],
        ),
        Section(
            heading="Health Screening",
            paragraphs=[
                "One health screening per member every 2 years is refunded up to 250 euro. The "
                "screening must be carried out by a provider on the published directory.",
                "Health screening does not count against the annual out-patient limit and is not "
                "subject to a waiting period.",
            ],
        ),
        Section(
            heading="Claiming",
            paragraphs=[
                "Claims are submitted through the member portal with a receipt showing the provider "
                "name, the date and the amount paid. Claims must be submitted within 6 months of the "
                "treatment date.",
                "A waiting period of 13 weeks applies to all benefits on this schedule for new members, "
                "other than health screening.",
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# 6, 7, 8 — the three specialist schedules.
# ---------------------------------------------------------------------------

MATERNITY = MockDocument(
    filename="Maternity and Newborn Benefits.docx",
    title="Maternity and Newborn Benefits",
    department="Claims",
    access_group="KH_CLAIMS_USERS",
    classification="Internal",
    version="2026.1",
    owner="Maternity Benefits Team",
    intro=(
        "Maternity and newborn entitlements across the antenatal, delivery and postnatal stages, "
        "including the cover a newborn holds from birth. Effective 1 April 2026."
    ),
    sections=[
        Section(
            heading="Waiting Period",
            paragraphs=[
                "A waiting period of 52 weeks applies to all maternity benefits. The waiting period "
                "runs from the member's join date and must be complete before the expected date of "
                "delivery, not before conception.",
                "Antenatal care delivered during the waiting period is claimable under Everyday "
                "Health Cashback as a consultant consultation, subject to that plan's own limits.",
            ],
        ),
        Section(
            heading="Antenatal Care",
            table=[
                ["Benefit", "Refund", "Frequency limit"],
                ["Consultant obstetrician visit", "110 euro per visit", "8 per pregnancy"],
                ["Midwife-led clinic visit", "60 euro per visit", "10 per pregnancy"],
                ["Dating and anomaly ultrasound scan", "Covered in full", "3 per pregnancy"],
                ["Additional ultrasound scan", "Up to 140 euro", "2 per pregnancy"],
                ["Antenatal class", "Up to 200 euro per pregnancy", "1 course"],
            ],
        ),
        Section(
            heading="Delivery",
            paragraphs=[
                "Delivery in a participating hospital is covered in full in semi-private "
                "accommodation. Private accommodation is covered in full where medically indicated "
                "and pre-authorised.",
                "The in-patient excess of 150 euro set out in Meridian Hospital Cover does not apply "
                "to a maternity admission.",
            ],
            table=[
                ["Delivery type", "Entitlement", "Condition"],
                ["Vaginal delivery", "Covered in full", "Pre-authorisation required"],
                ["Elective caesarean section", "Covered in full", "Pre-authorisation required"],
                ["Emergency caesarean section", "Covered in full", "Authorised retrospectively"],
                ["Home birth with registered midwife", "Up to 2,400 euro", "Pre-authorisation required"],
            ],
        ),
        Section(
            heading="Postnatal and Newborn Cover",
            paragraphs=[
                "A newborn is covered from birth for 13 weeks under the mother's membership without "
                "a separate waiting period. The newborn must be added to the policy within those 13 "
                "weeks for cover to continue.",
            ],
            table=[
                ["Benefit", "Refund", "Frequency limit"],
                ["Postnatal midwife home visit", "70 euro per visit", "5 per birth"],
                ["Newborn hearing screening", "Covered in full", "1 per birth"],
                ["Neonatal intensive care", "Covered in full, no day limit", "Pre-authorisation not required"],
                ["Postnatal physiotherapy", "45 euro per session", "6 per birth"],
                ["Lactation consultant", "60 euro per visit", "3 per birth"],
            ],
        ),
        Section(
            heading="Exclusions",
            paragraphs=[
                "Fertility treatment is not covered under this schedule and is not covered under any "
                "Corevita or Meridian plan.",
                "Elective delivery outside the Republic of Ireland is excluded unless pre-authorised "
                "on medical grounds.",
            ],
        ),
    ],
)


MENTAL_WELLBEING = MockDocument(
    filename="Mental Wellbeing Pathway.docx",
    title="Mental Wellbeing Pathway",
    department="Clinical",
    access_group="KH_CLINICAL_USERS",
    classification="Internal",
    version="2026.1",
    owner="Clinical Services Team",
    intro=(
        "The stepped pathway for mental health support, from self-referred counselling through to "
        "in-patient psychiatric admission, and what each step covers. Effective 1 April 2026."
    ),
    sections=[
        Section(
            heading="Step 1 — Self-Referred Counselling",
            paragraphs=[
                "Members may self-refer for counselling without a GP referral and without "
                "pre-authorisation. Six sessions per member per year are covered in full with an "
                "accredited counsellor on the published directory.",
                "There is no waiting period on Step 1. It is available from the first day of cover.",
            ],
        ),
        Section(
            heading="Step 2 — Referred Psychological Therapy",
            paragraphs=[
                "Step 2 requires a GP referral. It covers structured psychological therapy delivered "
                "by a chartered psychologist.",
            ],
            table=[
                ["Benefit", "Refund", "Frequency limit"],
                ["Cognitive behavioural therapy session", "70 euro per session", "12 per year"],
                ["Psychological assessment", "Up to 350 euro", "1 per year"],
                ["Group therapy session", "35 euro per session", "16 per year"],
                ["Family therapy session", "80 euro per session", "8 per year"],
            ],
        ),
        Section(
            heading="Step 3 — Psychiatric Care",
            paragraphs=[
                "Step 3 requires a consultant referral and pre-authorisation. A waiting period of 26 "
                "weeks applies for new members.",
            ],
            table=[
                ["Benefit", "Entitlement", "Condition"],
                ["Consultant psychiatrist consultation", "130 euro per visit", "6 per year"],
                ["Day-patient psychiatric programme", "Up to 40 days per year", "Pre-authorisation required"],
                ["In-patient psychiatric admission", "Up to 100 days per year", "Pre-authorisation required"],
                ["Addiction treatment programme", "Up to 91 days per year", "Pre-authorisation required"],
            ],
        ),
        Section(
            heading="Crisis Support",
            paragraphs=[
                "The 24-hour crisis line is available to all members at no cost and is not counted "
                "against any limit on this pathway.",
                "An emergency psychiatric admission is authorised retrospectively provided the "
                "Clinical Services Team is notified within 48 hours, mirroring the emergency "
                "admission rule in Meridian Hospital Cover.",
            ],
        ),
        Section(
            heading="Interaction With Other Benefits",
            paragraphs=[
                "Counselling sessions claimed under Step 1 do not count against the Everyday Health "
                "Cashback annual out-patient limit.",
                "Where a member is receiving both Step 2 therapy and physiotherapy under Everyday "
                "Health Cashback, the two limits are assessed separately and do not offset.",
            ],
        ),
    ],
)


OPTICAL_AUDIOLOGY = MockDocument(
    filename="Optical and Audiology Schedule.docx",
    title="Optical and Audiology Schedule",
    department="Claims",
    access_group="KH_CLAIMS_USERS",
    classification="Internal",
    version="2026.1",
    owner="Everyday Benefits Team",
    intro=(
        "Optical and hearing entitlements, including the appliance limits that apply to glasses, "
        "contact lenses and hearing aids. Effective 1 February 2026."
    ),
    sections=[
        Section(
            heading="Optical Benefits",
            paragraphs=[
                "Optical benefits carry a waiting period of 13 weeks for new members and do not "
                "require a GP referral or pre-authorisation."
            ],
            table=[
                ["Benefit", "Refund", "Frequency limit"],
                ["Eye examination", "Covered in full", "1 per year"],
                ["Prescription glasses, frames and lenses", "Up to 180 euro", "1 every 2 years"],
                ["Contact lenses", "Up to 150 euro per year", "no frequency limit"],
                ["Retinal photography", "Up to 45 euro", "1 per year"],
                ["Visual field test", "Up to 60 euro", "1 per year"],
            ],
        ),
        Section(
            heading="Audiology Benefits",
            table=[
                ["Benefit", "Refund", "Frequency limit"],
                ["Hearing assessment", "Covered in full", "1 per year"],
                ["Hearing aid, per ear", "Up to 900 euro", "1 per ear every 4 years"],
                ["Hearing aid repair", "Up to 120 euro", "2 per year"],
                ["Tinnitus management programme", "Up to 300 euro", "1 every 2 years"],
            ],
        ),
        Section(
            heading="Laser Eye Surgery",
            paragraphs=[
                "Laser eye surgery is refunded at 40% up to a lifetime limit of 1,100 euro per member, "
                "subject to pre-authorisation and to a waiting period of 24 months.",
                "Cataract removal is not claimed under this schedule. It is a day-case procedure and "
                "is covered under the Meridian Day-Case Schedule.",
            ],
        ),
        Section(
            heading="Exclusions",
            paragraphs=[
                "Non-prescription sunglasses, lens tints and anti-glare coatings applied for comfort "
                "rather than on prescription are excluded.",
                "Replacement of lost or damaged glasses inside the 2-year frequency limit is excluded.",
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# 9 — the procedure document. It cites vocabulary from every schedule above,
# which is what stops the graph from splitting into per-product islands.
# ---------------------------------------------------------------------------

CLAIMS_PROCEDURE = MockDocument(
    filename="Corevita Claims Handling Procedure.docx",
    title="Corevita Claims Handling Procedure",
    department="Operations",
    access_group="KH_OPERATIONS",
    classification="Internal",
    version="2026.4",
    owner="Claims Operations",
    intro=(
        "How a claim is received, assessed, paid or declined across every Corevita and Meridian "
        "schedule, and the service standards that apply at each step. This procedure governs the "
        "benefit schedules; where a schedule and this procedure disagree, the schedule wins on "
        "entitlement and this procedure wins on process. Effective 1 April 2026."
    ),
    sections=[
        Section(
            heading="Submission Routes",
            table=[
                ["Route", "Used for", "Service standard"],
                ["Member portal", "All out-patient and dental claims", "5 working days"],
                ["Direct settlement", "In-patient and day-case admissions", "Settled with the hospital"],
                ["Paper claim form", "Claims older than 6 months, appeals", "15 working days"],
                ["Provider-submitted claim", "Participating hospitals and clinics", "10 working days"],
            ],
        ),
        Section(
            heading="The Six-Month Submission Window",
            paragraphs=[
                "Every schedule applies the same submission window: a claim must reach Corevita "
                "within 6 months of the treatment date. The window runs from the date of treatment, "
                "not the date the member paid the provider.",
                "A late claim is declined without assessment. A member may appeal a late decline "
                "where the delay was caused by the provider, and the appeal is decided within 15 "
                "working days.",
            ],
        ),
        Section(
            heading="Assessment Steps",
            paragraphs=[
                "Step 1 — confirm membership was active on the treatment date and that the relevant "
                "waiting period was complete.",
                "Step 2 — confirm the benefit exists on the member's schedule and that the frequency "
                "limit has not already been used.",
                "Step 3 — where the schedule requires pre-authorisation, confirm an authorisation "
                "reference exists and covers the treatment actually delivered.",
                "Step 4 — apply the excess and any co-payment, then calculate the refund at the rate "
                "the schedule sets.",
                "Step 5 — pay the member, or pay the provider directly where direct settlement applies.",
            ],
        ),
        Section(
            heading="Excess and Co-payment Rules",
            paragraphs=[
                "Only one excess applies per admission. Where a day case converts to an overnight "
                "stay, the in-patient excess of 150 euro replaces the day-case excess of 75 euro and "
                "is not charged in addition to it.",
                "A co-payment listed on the Meridian Day-Case Schedule applies in addition to the "
                "excess and is not refundable.",
                "The Everyday Health Cashback out-patient excess of 1 euro is applied once per "
                "membership year to the first claim only.",
            ],
        ),
        Section(
            heading="Pre-Authorisation",
            paragraphs=[
                "Pre-authorisation is a clinical decision, not a payment guarantee. An authorisation "
                "confirms the treatment is covered; the claim is still assessed against the frequency "
                "limit and the annual limit when it is submitted.",
                "Planned in-patient admissions, major restorative and orthodontic dental treatment, "
                "Step 3 psychiatric care, laser eye surgery and the listed day-case procedures all "
                "require pre-authorisation.",
                "Emergency admissions are authorised retrospectively where the member notifies the "
                "relevant benefits team within 48 hours.",
            ],
        ),
        Section(
            heading="Declines and Appeals",
            table=[
                ["Decline reason", "Appealable", "Evidence needed"],
                ["Waiting period not complete", "Yes", "Proof of prior continuous cover"],
                ["Frequency limit already used", "No", "Not applicable"],
                ["Annual limit exhausted", "No", "Not applicable"],
                ["No pre-authorisation on file", "Yes", "Clinical justification from the consultant"],
                ["Treatment excluded by the schedule", "Yes", "Consultant referral and treatment plan"],
                ["Claim outside the 6-month window", "Yes", "Evidence the provider caused the delay"],
            ],
        ),
        Section(
            heading="Service Standards",
            paragraphs=[
                "Portal claims are assessed within 5 working days of receipt. Paper claims and "
                "appeals are decided within 15 working days.",
                "Where a claim is held for further evidence, the member is contacted within 3 working "
                "days and the clock is paused until the evidence arrives.",
                "Pre-authorisation requests are decided within 5 working days, or within 24 hours "
                "where the treating consultant marks the request urgent.",
            ],
        ),
    ],
)


ALL_DOCUMENTS: list[MockDocument] = [
    DENTAL_ESSENTIAL,
    DENTAL_COMPLETE,
    HOSPITAL_COVER,
    DAY_CASE_SCHEDULE,
    EVERYDAY_CASHBACK,
    MATERNITY,
    MENTAL_WELLBEING,
    OPTICAL_AUDIOLOGY,
    CLAIMS_PROCEDURE,
]
