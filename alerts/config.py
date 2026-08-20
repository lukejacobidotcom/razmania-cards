"""
What counts as a sale, and what to call it in a text message.

TWO SWOOGO EVENTS, NOT ONE. Attendee tickets and the Sponsor a Kid field live on
event 370376; exhibitor booths are a completely separate event, 372565, with its
own registration form. Nothing joins them, so both are swept every run.

EVERY ID BELOW IS A SWOOGO FIELD ID AND THEY ARE NOT STABLE. Rebuilding or
cloning a field in Swoogo mints a new id, and the old one silently stops
matching. That is the same trap the Sponsor a Kid page hit with its price
ladder. If a text ever arrives with the right money and a vague description,
this table is what has drifted — reread the ids from the registration form.

Nothing here is load-bearing for the MONEY. The amount always comes from the
registrant's own `individual_gross`, so a stale label produces a slightly
vaguer sentence, never a wrong dollar figure.
"""

EVENTS = [
    {
        "id": 370376,
        "kind": "ticket",
        "prefix": "RazMania",
        # Quantity fields on the main registration form. The registrant record
        # carries the chosen quantity directly ("0".."10"), which is why these
        # do not need the line-item table to be described.
        "items": {
            "c_8915620": "VIP",
            "c_8915595": "Adult",
            "c_8915600": "Child",
            "c_8940298": "Partners Circle",
            "c_9161333": "Sponsor a Kid",
        },
    },
    {
        "id": 372565,
        "kind": "exhibitor",
        "prefix": "RazMania booth",
        # Booths are `number` fields, so the registrant carries a bare count
        # ("8") or an empty string. Note these price PER TABLE: the line item
        # reports a unit price and a quantity, unlike the ticket dropdowns
        # above, which report a line total. Another reason the dollar figure is
        # read off individual_gross rather than recomputed here.
        "items": {
            "c_8978574": "Front-of-show table",
            "c_8978579": "Standard table",
            "c_8978580": "Premium table",
            "c_8915122": "Extra table",
        },
    },
]


def event(event_id):
    for e in EVENTS:
        if e["id"] == event_id:
            return e
    return {"id": event_id, "kind": "unknown", "prefix": "RazMania", "items": {}}


def fields_for(e):
    """The registrant fields one sweep of this event has to ask for."""
    base = ("id,first_name,last_name,email,company,registration_status,"
            "payment_status,individual_gross,group_gross,created_at,updated_at")
    return ",".join([base] + sorted(e["items"]))
