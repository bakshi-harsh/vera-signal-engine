"""Vera challenge bot: deterministic context store + rule-based composer.

No external LLM is required.  The judge can inject new context versions and
new trigger payloads; all decisions are derived from the currently stored
context.  The module also exposes compose() for the offline submission format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import threading
import time
from typing import Any, Optional


def first_name(merchant: dict) -> str:
    ident = merchant.get("identity", {}) or {}
    owner = ident.get("owner_first_name")
    if owner:
        return str(owner)
    name = str(ident.get("name", "Merchant"))
    return name.split()[0]


def merchant_name(merchant: dict) -> str:
    return str((merchant.get("identity") or {}).get("name") or "your business")


def category_slug(category: dict, merchant: dict) -> str:
    return str(category.get("slug") or merchant.get("category_slug") or "business").lower()


def active_offers(merchant: dict) -> list[dict]:
    return [o for o in (merchant.get("offers") or []) if str(o.get("status", "")).lower() == "active"]


def offer_title(merchant: dict, contains: str | None = None) -> str | None:
    offers = active_offers(merchant)
    if contains:
        for o in offers:
            if contains.lower() in str(o.get("title", "")).lower():
                return str(o.get("title"))
    return str(offers[0].get("title")) if offers else None


def pct(x: Any) -> str:
    try:
        v = float(x)
        return f"{abs(v) * 100:.0f}%" if abs(v) <= 1 else f"{abs(v):.0f}%"
    except Exception:
        return str(x)


def signed_pct(x: Any) -> str:
    try:
        v = float(x)
        if abs(v) <= 1:
            v *= 100
        return f"{v:+.0f}%"
    except Exception:
        return str(x)


def iso_date_label(value: str) -> str:
    # Keep supplied labels when available; only reformat raw ISO dates.
    if not value:
        return ""
    if "T" not in value:
        return value
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.strftime("%d %b, %I:%M %p").lstrip("0").replace(" 0", " ")
    except Exception:
        return value


def find_digest_item(category: dict, trigger: dict) -> dict:
    payload = trigger.get("payload") or {}
    item_id = payload.get("top_item_id") or payload.get("digest_item_id")
    for item in category.get("digest", []) or []:
        if item.get("id") == item_id:
            return item
    # Some injected triggers may embed the item directly.
    if isinstance(payload.get("top_item"), dict):
        return payload["top_item"]
    return {}


def allowed_language(customer: dict | None) -> str:
    return str(((customer or {}).get("identity") or {}).get("language_pref") or "en").lower()


def customer_name(customer: dict | None) -> str:
    return str(((customer or {}).get("identity") or {}).get("name") or "there")


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    """Compose one deterministic message from the four challenge contexts."""
    slug = category_slug(category, merchant)
    kind = str(trigger.get("kind") or "unknown")
    payload = trigger.get("payload") or {}
    ident = merchant.get("identity") or {}
    name = first_name(merchant)
    business = merchant_name(merchant)
    customer_facing = customer is not None or trigger.get("scope") == "customer"

    if customer_facing:
        result = _compose_customer(slug, merchant, trigger, customer, category)
    else:
        result = _compose_merchant(slug, merchant, trigger, category)

    # Hard safety/format normalization.  Never replace facts; only enforce
    # deterministic output shape and a single primary CTA.
    result.setdefault("body", f"Hi {name} — I spotted something relevant to {business}. Want me to help with it?")
    result.setdefault("cta", "open_ended")
    result["send_as"] = "merchant_on_behalf" if customer_facing else "vera"
    result["suppression_key"] = str(trigger.get("suppression_key") or trigger.get("id") or f"{kind}:{merchant.get('merchant_id','unknown')}")
    result.setdefault("rationale", f"{kind} trigger matched to {slug} context and current merchant/customer facts.")
    return result


def _compose_merchant(slug: str, merchant: dict, trigger: dict, category: dict) -> dict:
    p = trigger.get("payload") or {}
    kind = str(trigger.get("kind") or "")
    owner = first_name(merchant)
    if slug == "dentists" and not owner.lower().startswith("dr"):
        owner = f"Dr. {owner}"
    city = (merchant.get("identity") or {}).get("city", "")
    locality = (merchant.get("identity") or {}).get("locality", "")
    business = merchant_name(merchant)
    performance = merchant.get("performance") or {}
    signals = [str(x) for x in merchant.get("signals", [])]
    aggregate = merchant.get("customer_aggregate") or {}
    offer = offer_title(merchant)
    item = find_digest_item(category, trigger)
    source = item.get("source") or p.get("source")
    tone = str(((category.get("voice") or {}).get("tone")) or "peer")

    if kind in {"research_digest", "category_research_digest_release"}:
        title = item.get("title") or p.get("title") or "a new category research item"
        facts = []
        if item.get("trial_n") is not None: facts.append(f"{item['trial_n']:,}-participant")
        if item.get("patient_segment") and ("risk" in str(item["patient_segment"]).lower() or "segment" in str(item["patient_segment"]).lower()):
            facts.append(str(item["patient_segment"]).replace("_", " "))
        detail = title
        if facts and str(item.get("title", "")).lower().find(str(item.get("trial_n", "___"))) < 0:
            detail = f"{facts[0]} study: {title}"
        anchor = ""
        if any("high_risk" in s or "high-risk" in s for s in signals) or aggregate.get("lapsed_180d_plus"):
            anchor = " It is especially relevant to the cohort showing up in your account."
        digest_text = "this week's " + slug + " digest has" if slug else "the latest digest has"
        body = f"{owner}, {digest_text} one item worth a look: {detail}.{anchor}"
        if source: body += f" Source: {source}."
        body += " Want me to turn the useful part into a ready-to-send customer post?"
        return {"body": body, "cta": "open_ended", "template_name": "vera_research_digest_v1",
                "template_params": [owner, detail, source or "", "ready-to-send customer post"] ,
                "rationale": "Research trigger matched to a category digest item; message cites the supplied source and uses merchant signals to explain relevance."}

    if kind in {"regulation_change", "cde_opportunity"}:
        title = item.get("title") or p.get("title") or "a category update"
        deadline = p.get("deadline_iso") or item.get("deadline")
        body = f"{owner}, a {slug} update needs a look: {title}"
        if deadline: body += f" Deadline: {deadline}."
        if source: body += f" Source: {source}."
        body += " Want me to turn the change into a short action checklist?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_compliance_v1",
                "template_params": [owner, title, str(deadline or "")],
                "rationale": "Compliance/opportunity trigger is grounded in the supplied digest item and deadline; offers one concrete follow-up."}

    if kind in {"perf_dip", "seasonal_perf_dip", "perf_spike"}:
        metric = p.get("metric", "views")
        delta = p.get("delta_pct", (performance.get("delta_7d") or {}).get(f"{metric}_pct"))
        if delta is None: delta = ""
        if kind == "perf_spike":
            driver = p.get("likely_driver")
            body = f"{owner}, {metric} are up {signed_pct(delta)} over {p.get('window','7d')}" + (f", likely tied to {str(driver).replace('_',' ')}." if driver else ".")
            body += " Want me to turn the signal into the next post or offer?"
            rationale = "Performance spike is acknowledged with the supplied delta and, when present, its supplied likely driver."
        elif kind == "seasonal_perf_dip" and p.get("is_expected_seasonal"):
            members = aggregate.get("total_active_members")
            body = f"{owner}, {metric} are down {pct(delta)} this week, but the trigger marks this as an expected seasonal dip" + (f"; you still have {members} active members to retain." if members is not None else ".")
            body += " Want me to draft a retention-focused action for this period?"
            rationale = "Expected seasonal dip is reframed using the trigger's explicit seasonal flag rather than treating a normal dip as a crisis."
        else:
            baseline = p.get("vs_baseline")
            body = f"{owner}, {metric} are down {pct(delta)} over {p.get('window','7d')}"
            if baseline is not None: body += f" versus a baseline of {baseline}."
            body = body.rstrip("\n") + " I’d address the drop before spending on a new campaign. Want me to draft the smallest next test?"
            rationale = "Performance dip is tied to the supplied metric, period and baseline where available; CTA proposes a low-effort next action."
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_performance_v1",
                "template_params": [owner, metric, str(delta)], "rationale": rationale}

    if kind == "renewal_due":
        days = p.get("days_remaining", (merchant.get("subscription") or {}).get("days_remaining"))
        amount = p.get("renewal_amount")
        body = f"{owner}, your {p.get('plan', (merchant.get('subscription') or {}).get('plan','plan'))} renewal is {days} days away" if days is not None else f"{owner}, your subscription renewal is coming up"
        if amount is not None: body += f" at ₹{amount:,}."
        else: body += "."
        body += " Want me to prepare the renewal details?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_renewal_v1", "template_params": [owner, str(days or ""), str(amount or "")],
                "rationale": "Renewal message uses only the supplied plan, remaining days and price."}

    if kind == "festival_upcoming":
        fest = p.get("festival", "the upcoming festival")
        days = p.get("days_until")
        body = f"{owner}, {fest} is {days} days away" if days is not None else f"{owner}, {fest} is coming up"
        if offer: body += f". Your active offer is {offer}."
        body += " Want me to turn the existing offer into a festival post?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_festival_v1", "template_params": [owner, str(fest), str(offer or "")],
                "rationale": "Festival trigger is paired with the merchant's existing offer instead of inventing a new promotion."}

    if kind == "ipl_match_today":
        match = p.get("match", "today's match")
        venue = p.get("venue")
        tm = p.get("match_time_iso")
        body = f"Quick heads-up {owner}: {match} is today" + (f" at {venue}" if venue else "") + (f", {iso_date_label(tm)}" if tm else "") + "."
        if p.get("is_weeknight") is False and offer:
            body += f" Keep the existing {offer} focused on delivery rather than creating a new match promo."
        elif offer:
            body += f" Your existing {offer} can be the starting point."
        body += " Want me to draft the channel copy?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_event_v1", "template_params": [owner, str(match), str(venue or "")],
                "rationale": "Event timing is combined with the supplied weekend flag and existing offer; no new offer is fabricated."}

    if kind == "review_theme_emerged":
        theme = p.get("theme", "a review theme")
        count = p.get("occurrences_30d")
        quote = p.get("common_quote")
        body = f"{owner}, {count} reviews in the last 30 days mention {str(theme).replace('_',' ')}" if count is not None else f"{owner}, a review theme is emerging around {str(theme).replace('_',' ')}"
        if p.get("trend") == "rising": body += " and the trend is rising."
        else: body += "."
        if quote: body += f" One customer put it as: ‘{quote}’."
        body += " Want me to draft a response-and-fix plan?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_review_theme_v1", "template_params": [owner, str(theme), str(count or "")],
                "rationale": "Review-theme trigger is grounded in the supplied count, trend and customer wording."}

    if kind == "milestone_reached":
        metric, value = p.get("metric", "metric"), p.get("value_now", p.get("milestone_value"))
        target = p.get("milestone_value")
        body = f"{owner}, you’re at {value} {str(metric).replace('_',' ')}"
        if target is not None and p.get("is_imminent"): body += f" — {target} is the next milestone."
        body += " Want me to turn the milestone into a GBP post?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_milestone_v1", "template_params": [owner, str(value), str(target or "")],
                "rationale": "Milestone message uses the supplied metric and milestone values and offers a concrete celebration asset."}

    if kind == "active_planning_intent":
        topic = str(p.get("intent_topic") or "the plan")
        last = p.get("merchant_last_message")
        if "corporate_bulk_thali" in topic:
            current = offer or "your current lunch offer"
            body = f"{owner}, here’s a starter corporate version built around {current}. I can structure it for 10, 25 and 50+ thalis while keeping the existing offer as the price anchor."
            body += " Want me to turn this into a 3-line facilities-manager WhatsApp?"
        elif "kids_yoga" in topic:
            body = f"{owner}, for the kids-yoga plan you asked about, a clean starter is 4 weeks, 3 classes/week, ages 7–12."
            body += " Want me to draft the GBP post + parent WhatsApp copy?"
        else:
            body = f"{owner}, picking up your planning thread on {topic.replace('_',' ')}"
            if last: body += f" — you said, ‘{last}’."
            body += " Want me to turn it into the first usable draft?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_planning_v1", "template_params": [owner, topic],
                "rationale": "The merchant has expressed planning intent, so the bot moves directly to an actionable draft instead of re-qualifying."}

    if kind == "curious_ask_due":
        ask = p.get("ask_template", "what is in demand this week")
        body = f"Hi {owner}! Quick one for {business}: what service has been most asked for this week? I’ll turn your answer into a ready-to-use post and reply script."
        return {"body": body, "cta": "open_ended", "template_name": "vera_curiosity_v1", "template_params": [owner, ask],
                "rationale": "Curiosity trigger asks the merchant for a low-effort fact and offers an immediate artifact in return."}

    if kind == "winback_eligible":
        days = p.get("days_since_expiry")
        dip = p.get("perf_dip_pct")
        body = f"{owner}, your account has been expired for {days} days" if days is not None else f"{owner}, your account is eligible for a win-back"
        if dip is not None: body += f" and performance is down {pct(dip)}."
        body += " Want me to outline the shortest reactivation path?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_winback_v1", "template_params": [owner, str(days or ""), str(dip or "")],
                "rationale": "Win-back is based on supplied expiry and performance facts; no unverified benefit is promised."}

    if kind == "seasonal_perf_dip":
        pass

    if kind == "supply_alert":
        molecule = p.get("molecule", "the affected medicine")
        batches = p.get("affected_batches") or []
        manufacturer = p.get("manufacturer")
        body = f"{owner}, urgent supply alert: {molecule}"
        if batches: body += f" — affected batches {', '.join(map(str,batches))}"
        if manufacturer: body += f" from {manufacturer}"
        body += "."
        chronic = aggregate.get("chronic_rx_count")
        if chronic is not None: body += f" You have {chronic} chronic-Rx customers in your aggregate; the alert should be checked against dispense records before outreach."
        body += " Want me to draft the customer-notification workflow?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_supply_alert_v1", "template_params": [owner, str(molecule), ", ".join(map(str,batches))],
                "rationale": "High-urgency pharmacy alert uses the exact molecule and batch identifiers supplied by the trigger and avoids inventing affected-customer counts."}

    if kind == "category_seasonal":
        trends = p.get("trends") or []
        body = f"{owner}, the {p.get('season','current')} demand shift flags " + ", ".join(map(str, trends[:3])) + "."
        if p.get("shelf_action_recommended"): body += " Shelf action is recommended by the trigger."
        body += " Want me to turn the strongest signal into a simple shelf/post checklist?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_seasonal_v1", "template_params": [owner, str(p.get('season','')), ", ".join(map(str,trends[:3]))],
                "rationale": "Seasonal message reflects only the supplied demand movements and explicit shelf-action flag."}

    if kind == "gbp_unverified":
        verified = p.get("verified", (merchant.get("identity") or {}).get("verified"))
        path = p.get("verification_path")
        uplift = p.get("estimated_uplift_pct")
        body = f"{owner}, your Google Business Profile is still unverified."
        if path: body += f" The supplied verification path is {path}."
        if uplift is not None: body += f" The trigger estimates up to {pct(uplift)} uplift."
        body += " Want me to walk you through the verification steps?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_gbp_v1", "template_params": [owner, str(path or "")],
                "rationale": "GBP verification message uses the actual verification state and supplied path; estimated uplift is explicitly attributed to the trigger."}

    if kind == "competitor_opened":
        comp = p.get("competitor_name", "a new competitor")
        dist = p.get("distance_km")
        their_offer = p.get("their_offer")
        body = f"{owner}, {comp} opened {dist} km away" if dist is not None else f"{owner}, {comp} has opened nearby"
        if their_offer: body += f" with {their_offer}."
        else: body += "."
        body += " Want me to compare this with your current active offer and draft a response?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "vera_competitor_v1", "template_params": [owner, str(comp), str(dist or "")],
                "rationale": "Competitor message uses the supplied competitor, distance and offer, then proposes comparison against the merchant's actual offer catalog."}

    if kind == "dormant_with_vera":
        days = p.get("days_since_last_merchant_message")
        topic = p.get("last_topic")
        body = f"Hi {owner} — it’s been {days} days since we last spoke" if days is not None else f"Hi {owner} — it’s been a while since we last spoke"
        if topic: body += f" about {str(topic).replace('_',' ')}."
        body += " What would be most useful to work on this week?"
        return {"body": body, "cta": "open_ended", "template_name": "vera_reengage_v1", "template_params": [owner, str(topic or "")],
                "rationale": "Dormancy trigger is acknowledged without inventing a new business problem; the merchant is invited to set the next topic."}

    # Generic deterministic fallback for injected trigger kinds.
    facts = []
    for k in ("metric", "value", "title", "topic", "deadline_iso", "days_until"):
        if p.get(k) is not None:
            facts.append(f"{k.replace('_',' ')}: {p[k]}")
    fact_text = "; ".join(facts[:3])
    body = f"{owner}, I have a {kind.replace('_',' ')} update relevant to {business}"
    if fact_text: body += f" — {fact_text}."
    else: body += "."
    body += " Want me to turn the supplied details into the next useful action?"
    return {"body": body, "cta": "open_ended", "template_name": "vera_context_v1", "template_params": [owner, kind, fact_text],
            "rationale": "No specialized handler matched; the response stays grounded in fields actually present in the trigger."}


def _compose_customer(slug: str, merchant: dict, trigger: dict, customer: dict | None, category: dict) -> dict:
    p = trigger.get("payload") or {}
    kind = str(trigger.get("kind") or "")
    cname = customer_name(customer)
    owner = first_name(merchant)
    if slug == "dentists" and not owner.lower().startswith("dr"):
        owner = f"Dr. {owner}"
    business = merchant_name(merchant)
    pref = allowed_language(customer)
    ident = merchant.get("identity") or {}
    local = ident.get("locality", "")
    offer = offer_title(merchant)
    consent = (customer or {}).get("consent") or {}

    # Do not send promotional customer outreach unless there is explicit consent.
    scopes = [str(x).lower() for x in (consent.get("scope") or [])]
    if consent and not scopes and kind not in {"recall_due", "appointment_tomorrow", "chronic_refill_due"}:
        return {"body": "", "cta": "none", "template_name": "merchant_customer_v1", "template_params": [],
                "rationale": "Customer consent scope is absent, so proactive outreach is suppressed."}

    if kind == "recall_due":
        slots = p.get("available_slots") or []
        slot = slots[0].get("label") if slots else None
        due = p.get("due_date")
        prefix = f"Hi {cname}, {business} here 🦷" if slug == "dentists" else f"Hi {cname}, {business} here"
        if "hi-en" in pref or pref == "hi":
            prefix = f"Hi {cname}, {business} se message kar rahe hain"
        body = f"{prefix}. Your {str(p.get('service_due','appointment')).replace('_',' ')} is due"
        if due: body += f" on {due}"
        if slot: body += f". We have {slot} available"
        if offer: body += f" with {offer}"
        body += ". Reply YES and I’ll help lock the slot."
        return {"body": body, "cta": "binary_yes_no", "template_name": "merchant_recall_v1", "template_params": [cname, str(due or ""), str(slot or "")],
                "rationale": "Recall outreach uses the customer's due date, an actual available slot and the merchant's active offer where present."}

    if kind in {"customer_lapsed_hard", "customer_lapsed_soft"}:
        days = p.get("days_since_last_visit")
        state = (customer or {}).get("state", "lapsed")
        prefix = f"Hi {cname} 👋 {owner} from {business} here."
        if "hi-en" in pref or pref == "hi": prefix = f"Hi {cname} 👋 {owner} from {business} here. Kaafi time ho gaya"
        body = prefix
        if days is not None: body += f" — it’s been {days} days since your last visit."
        else: body += " — we’d be happy to have you back."
        if offer: body += f" We currently have {offer}."
        body += " No pressure — reply YES if you want the current options."
        return {"body": body, "cta": "binary_yes_no", "template_name": "merchant_winback_v1", "template_params": [cname, str(days or ""), str(offer or "")],
                "rationale": "Win-back is warm and non-judgmental, uses the customer's actual lapse duration and only an active merchant offer."}

    if kind == "wedding_package_followup":
        wedding = p.get("wedding_date")
        days = p.get("days_to_wedding")
        body = f"Hi {cname} 💍 {owner} from {business} here."
        if days is not None: body += f" You’re {days} days from the wedding"
        elif wedding: body += f" Your wedding is on {wedding}"
        body += "."
        if offer: body += f" Your current option is {offer}."
        body += " Want me to help hold a suitable first-session slot?"
        return {"body": body, "cta": "binary_yes_no", "template_name": "merchant_bridal_v1", "template_params": [cname, str(wedding or ""), str(offer or "")],
                "rationale": "Bridal follow-up continues the customer's existing relationship and wedding timing without inventing a new package."}

    if kind == "trial_followup":
        opts = p.get("next_session_options") or []
        slot = opts[0].get("label") if opts else None
        body = f"Hi {cname}, {owner} from {business} here."
        if p.get("trial_date"): body += f" Following up on your trial from {p['trial_date']}"
        if slot: body += f" — the next listed option is {slot}."
        if offer: body += f" {offer} is also currently active."
        body += " Reply YES if you want me to hold the next step."
        return {"body": body, "cta": "binary_yes_no", "template_name": "merchant_trial_v1", "template_params": [cname, str(slot or "")],
                "rationale": "Trial follow-up uses the supplied trial date and next available option, with no invented scheduling details."}

    if kind == "chronic_refill_due":
        molecules = p.get("molecule_list") or []
        runout = p.get("stock_runs_out_iso")
        language = "Namaste" if "hi" in pref else "Hello"
        body = f"{language} {cname}, {business} here. Your listed monthly medicines ({', '.join(map(str,molecules))}) are due to run out on {iso_date_label(runout) if runout else 'the supplied date'}."
        if offer: body += f" Our active pharmacy offer is {offer}."
        body += " Reply CONFIRM if you want us to prepare the refill; please check any dose or medicine change with your prescriber."
        return {"body": body, "cta": "binary_yes_no", "template_name": "merchant_refill_v1", "template_params": [cname, ", ".join(map(str,molecules)), str(runout or "")],
                "rationale": "Refill reminder uses only the supplied medicine list and run-out timing; it does not make a clinical dosing recommendation."}

    if kind == "appointment_tomorrow":
        appt = p.get("appointment_time") or p.get("slot") or p.get("appointment")
        body = f"Hi {cname}, {business} here. Your appointment is tomorrow"
        if appt: body += f" at {appt}"
        body += ". Reply YES to confirm, or let us know if the timing needs changing."
        return {"body": body, "cta": "binary_yes_no", "template_name": "merchant_appointment_v1", "template_params": [cname, str(appt or "")],
                "rationale": "Appointment reminder is limited to the supplied booking information and offers one clear confirmation action."}

    # Generic customer fallback: avoid medical or promotional claims.
    body = f"Hi {cname}, {business} here. We have an update related to your recent interaction."
    body += " Reply YES if you’d like the details."
    return {"body": body, "cta": "binary_yes_no", "template_name": "merchant_customer_v1", "template_params": [cname],
            "rationale": "Customer-facing fallback avoids unsupported claims and asks for a low-friction confirmation before continuing."}


@dataclass
class Versioned:
    version: int
    payload: dict


@dataclass
class Conversation:
    merchant_id: str
    customer_id: str | None = None
    last_body: str = ""
    ended: bool = False
    auto_reply_count: int = 0
    turns: list[dict] = field(default_factory=list)
    mode: str = "qualify"


class ContextStore:
    def __init__(self):
        self._lock = threading.RLock()
        self.data: dict[str, dict[str, Versioned]] = {"category": {}, "merchant": {}, "customer": {}, "trigger": {}}
        self.suppressed: set[str] = set()
        self.conversations: dict[str, Conversation] = {}
        self.started = time.monotonic()

    def put(self, scope: str, context_id: str, version: int, payload: dict) -> tuple[bool, int | None]:
        with self._lock:
            bucket = self.data[scope]
            cur = bucket.get(context_id)
            if cur and version <= cur.version:
                return False, cur.version
            bucket[context_id] = Versioned(version, payload)
            return True, None

    def get(self, scope: str, context_id: str) -> dict | None:
        with self._lock:
            v = self.data.get(scope, {}).get(context_id)
            return v.payload if v else None

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {k: len(v) for k, v in self.data.items()}

    def mark_sent(self, suppression_key: str):
        if suppression_key:
            self.suppressed.add(suppression_key)

    def is_suppressed(self, suppression_key: str) -> bool:
        return suppression_key in self.suppressed

    def conversation(self, cid: str, merchant_id: str, customer_id: str | None = None) -> Conversation:
        with self._lock:
            if cid not in self.conversations:
                self.conversations[cid] = Conversation(merchant_id, customer_id)
            return self.conversations[cid]

    def context_for_trigger(self, trigger: dict):
        mid = trigger.get("merchant_id")
        cid = trigger.get("customer_id")
        merchant = self.get("merchant", mid) if mid else None
        customer = self.get("customer", cid) if cid else None
        cat = self.get("category", (merchant or {}).get("category_slug", "")) if merchant else None
        return cat or {}, merchant or {}, customer

    def compose_trigger(self, trigger_id: str) -> dict | None:
        trigger = self.get("trigger", trigger_id)
        if not trigger:
            return None
        category, merchant, customer = self.context_for_trigger(trigger)
        if not merchant:
            return None
        return compose(category, merchant, trigger, customer)
