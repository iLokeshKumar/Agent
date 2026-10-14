"""One-shot, idempotent set-up of the ISM pilot (plan section 6).

Run from the backend folder, with the backend's own database:

    python scripts/setup_ism_pilot.py                 # templates + campaign + sequence
    python scripts/setup_ism_pilot.py --import-sheet  # also import the ISM's pilot sheet
    python scripts/setup_ism_pilot.py --import-sheet --audience   # and build the 3-wave audience
    python scripts/setup_ism_pilot.py --pipeline-target 15000000  # record the ISM's target (Rs)

What it creates (never sends anything):
  * 9 templates, all 'pending': 5 emails (2 A/B openers), 3 call scripts, 1 LinkedIn note.
    Every email carries [CONFIRM: ...] placeholders the ISM must replace in the
    console before the template can be approved — nothing can go out unedited.
  * The pilot campaign, ISM-managed (sends nothing until Approve), email-first,
    A/B on the opener, 3 waves.
  * The 8-touch / 29-day sequence: day 1 email, 4 call, 8 LinkedIn, 11 email,
    15 call, 18 email, 25 call, 29 email; never more than 2 touches in 7 days.
Re-running is safe: approved or edited templates are left alone.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CAMPAIGN_NAME = "ISM Pilot - Interactive displays - Education & Government, Tamil Nadu"
SHEET = Path(__file__).resolve().parents[2] / "campaign_phase0" / "pilot_candidates_education_government.csv"
SIGN = "<p>Regards,<br>[CONFIRM: sender name, title and mobile]<br>Yexis Electronics</p>"

TEMPLATES = [
    # (template_id, channel, subject, body)
    ("ism_disp_e1_a", "email", "Classroom displays at {{ company }}",
     "<p>Dear {{ greeting }},</p>"
     "<p>I am writing from Yexis Electronics, an authorised Samsung and HP distributor serving schools, "
     "colleges and government offices in Tamil Nadu and Kerala.</p>"
     "<p>Is upgrading classroom or meeting-room displays something {{ company }} is considering this academic year?</p>"
     "<p>If it is, I can share suitable options and indicative pricing for the number of rooms you have in mind.</p>" + SIGN),
    ("ism_disp_e1_b", "email", "A question about {{ company }}'s classrooms",
     "<p>Dear {{ greeting }},</p>"
     "<p>A short question from Yexis Electronics (authorised Samsung and HP distributor, Tamil Nadu and Kerala): "
     "who at {{ company }} looks after classroom technology such as projectors and displays?</p>"
     "<p>We work with institutions planning display upgrades and would like to share options with the right person.</p>" + SIGN),
    ("ism_disp_call1", "call", "Call 1 - first conversation",
     "Opener: \"Good morning, this is [your name] from Yexis Electronics, the Samsung and HP distributor in Chennai. "
     "I sent a note to {{ company }} about classroom displays - may I know who handles classroom technology or IT?\"\n"
     "Ask: number of classrooms / halls; what they use now (projector, board, TV); any plan this academic year; "
     "who decides and who signs purchase orders; the budget or tender timing.\n"
     "Get: the decision-maker's name, title, email and mobile.\n"
     "Next step: offer a short demo or site survey. Log every answer in the task outcome."),
    ("ism_disp_li", "linkedin", "LinkedIn - connect, no pitch",
     "Find the principal, IT head or administrator of {{ company }} on LinkedIn. View the profile, then connect with:\n"
     "\"Hello, I work with schools and colleges in Tamil Nadu on classroom technology at Yexis Electronics. Glad to connect.\"\n"
     "No pitch and no attachment in the connection note."),
    ("ism_disp_e2", "email", "Re: classroom displays at {{ company }}",
     "<p>Dear {{ greeting }},</p>"
     "<p>Following up on my earlier note about classroom displays.</p>"
     "<p>[CONFIRM: one real recent installation - institution (with their permission), number of displays, month. "
     "If there is none to quote yet, delete this paragraph.]</p>"
     "<p>Would a short demo at {{ company }} be useful? It takes about 20 minutes.</p>" + SIGN),
    ("ism_disp_call2", "call", "Call 2 - follow up on the email",
     "Opener: \"This is [your name] from Yexis Electronics - I emailed about classroom displays and offered a short demo. "
     "Did it reach the right person at {{ company }}?\"\n"
     "If interested: fix a date for the demo or site survey and log it as a Deal (stage Qualification) with the campaign.\n"
     "If not now: ask when to check back and log it in the outcome."),
    ("ism_disp_e3", "email", "Demo or site survey for {{ company }}",
     "<p>Dear {{ greeting }},</p>"
     "<p>If it helps your planning, we can do a classroom demo or a site survey at {{ company }}: we look at the rooms "
     "and suggest display sizes and placement.</p>"
     "<p>[CONFIRM: is the demo / site survey free of charge? State the terms or delete this line.]</p>"
     "<p>Would any day next week suit you?</p>" + SIGN),
    ("ism_disp_call3", "call", "Call 3 - last call",
     "Opener: \"[your name] from Yexis Electronics - last call from my side about classroom displays for {{ company }}.\"\n"
     "Ask: is there a plan this academic year or next? Who should we speak to then?\n"
     "Log the answer; if there is a later date, the agent's follow-up will use it."),
    ("ism_disp_e4", "email", "Close the file for {{ company }}?",
     "<p>Dear {{ greeting }},</p>"
     "<p>I have not heard back, so I assume display upgrades are not a priority at {{ company }} right now.</p>"
     "<p>Shall I close the file, or check back next term?</p>" + SIGN),
]

# step_number, delay from the previous touch (hours), channel, template
SEQUENCE = [
    (1, 72, "call", "ism_disp_call1"),      # day 4
    (2, 96, "linkedin", "ism_disp_li"),     # day 8
    (3, 72, "email", "ism_disp_e2"),        # day 11
    (4, 96, "call", "ism_disp_call2"),      # day 15
    (5, 72, "email", "ism_disp_e3"),        # day 18
    (6, 168, "call", "ism_disp_call3"),     # day 25
    (7, 96, "email", "ism_disp_e4"),        # day 29
]


def main(argv=None, session_factory=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--import-sheet", action="store_true", help="import campaign_phase0 pilot sheet and score")
    ap.add_argument("--audience", action="store_true", help="build the 3-wave audience from pilot accounts")
    ap.add_argument("--pipeline-target", type=float, default=None, help="ISM's pipeline target in Rs")
    ap.add_argument("--isrs", default="", help="comma-separated ISR names (else taken from the sheet)")
    args = ap.parse_args(argv)

    if session_factory is None:
        from app.core.db import SessionLocal as session_factory
    from app.models.enterprise import Campaign, CampaignContact, CampaignSequenceStep, TemplateRegistry
    db = session_factory()
    try:
        made = kept = 0
        for tid, ch, subj, body in TEMPLATES:
            t = db.get(TemplateRegistry, tid)
            if t is None:
                db.add(TemplateRegistry(template_id=tid, channel=ch,
                                        purpose=subj, language="en", body=body,
                                        approval_status="pending", version=1,
                                        variables_json={"kind": ch}))
                made += 1
            else:
                kept += 1   # never overwrite the ISM's edits or approvals
        db.commit()
        print(f"templates: {made} created, {kept} already present (left as they are)")

        c = db.query(Campaign).filter(Campaign.name == CAMPAIGN_NAME).first()
        brief = {
            "category": "Interactive displays", "verticals": ["Education", "Government"],
            "first_call_template": "ism_disp_call1",
            "regions": ["Tamil Nadu"], "window_days": 60, "waves": 3,
            "pipeline_target": args.pipeline_target,
            "isrs": [x.strip() for x in args.isrs.split(",") if x.strip()],
            "gates": {"max_hard_bounce_pct": 2.0, "min_positive_reply_pct": 2.5, "min_meeting_pct": 1.2,
                      "max_first_touch_hours_p1": 24, "min_days_between_waves": 5},
        }
        if c is None:
            from datetime import datetime, timezone
            import uuid
            c = Campaign(
                campaign_ref=f"ISM-{datetime.now(timezone.utc):%Y%m%d}-PILOT", name=CAMPAIGN_NAME,
                campaign_type="end_customer", entity="YEXIS-EL", status="draft",
                message_template_id="ism_disp_e1_a", default_channel="email", sequence_enabled=True,
                ab_test_json={"variants": [{"name": "A", "template_id": "ism_disp_e1_a"},
                                           {"name": "B", "template_id": "ism_disp_e1_b"}]},
                ism_managed=True, ism_brief_json=brief,
            )
            db.add(c)
            db.commit()
            print(f"campaign created: {c.campaign_ref} ({c.id})")
        else:
            old = dict(c.ism_brief_json or {})
            for k in ("pipeline_target", "isrs"):
                if brief[k]:
                    old[k] = brief[k]
            c.ism_brief_json = {**brief, **old}
            db.commit()
            print(f"campaign exists: {c.campaign_ref} ({c.id}) - brief kept, target/isrs updated if given")

        have = {s.step_number for s in db.query(CampaignSequenceStep).filter(CampaignSequenceStep.campaign_id == c.id).all()}
        for n, delay, ch, tid in SEQUENCE:
            if n not in have:
                db.add(CampaignSequenceStep(campaign_id=c.id, step_number=n, delay_hours=delay,
                                            template_id=tid, channel=ch, condition="no_response"))
        db.commit()
        print(f"sequence: {len(SEQUENCE)} follow-up steps in place")

        if args.import_sheet:
            from app.services.account_intel import import_accounts_csv, score_all
            r = import_accounts_csv(db, str(SHEET))
            print(f"pilot sheet: {r}; tiers {score_all(db)}")
            from app.models.enterprise import AccountProfile
            n_pilot = db.query(AccountProfile).filter(AccountProfile.pilot.is_(True)).count()
            print(f"accounts marked pilot_yes_no=yes: {n_pilot}")
            if n_pilot == 0:
                print("  -> the ISM has not marked any pilot accounts yet (pilot_yes_no column).")

        if args.audience:
            if c.ism_approved_ts:
                print("campaign already approved - audience not rebuilt")
            else:
                from app.agents.k2_audience_builder import K2AudienceBuilder
                r = K2AudienceBuilder().execute({"action": "build", "campaign_id": str(c.id), "source": "crm_accounts",
                                                 "regions": brief["regions"], "verticals": brief["verticals"],
                                                 "pilot_only": True, "waves": 3}, requested_by="setup_ism_pilot")
                print(f"audience: {r.status} {r.output or r.error}")
        total = db.query(CampaignContact).filter(CampaignContact.campaign_id == c.id).count()
        pending = [t.template_id for t in db.query(TemplateRegistry).filter(
            TemplateRegistry.template_id.in_([x[0] for x in TEMPLATES]), TemplateRegistry.approval_status == "pending").all()]
        print(f"\ncampaign {c.id}: {total} contacts; templates still pending: {len(pending)}")
        print("Next: open /ism/console -> Review & approve -> edit and approve each template -> Approve campaign ->\n"
              "      schedule the first send: POST /campaigns/{id}/schedule {template_id: ism_disp_e1_a, scheduled_at}.")
        return c.id
    finally:
        db.close()


if __name__ == "__main__":
    main()
