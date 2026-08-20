# Sale alerts — text on every RazMania sale

A five-minute cron that watches **both** RazMania Swoogo events and texts
everyone in `ALERT_TO` whenever a confirmed order total changes.

```
RazMania: Sam New bought 1 VIP - $47 | Paid | sam@example.com | $2,898 booked
RazMania: Jane Doe bought 2 Adult, 1 Sponsor a Kid - $227.70 | Paid | jane@example.com | $3,058 booked
RazMania booth: Media Reload (Eric Tinnin) bought 8 Standard table - $1,656 | Paid | eric@vendor.com | $28k booked
```

Tickets lead with the buyer's name; booths lead with the **company**, because on
the exhibitor form the vendor is the customer and the person is just the contact.

| | |
|---|---|
| `config.py` | which events, which fields, what to call them |
| `watch.py` | the poller — sweep, diff, compose, send, record |
| `swoogo.py` | ~100 lines of Swoogo REST, stdlib only |
| `sms.py` | Textbelt, or a carrier email-to-SMS gateway |
| `schema.sql` | two tables in the existing `razmania-db` |
| `test_watch.py` | the whole alert logic, offline, sending nothing |

No new Python dependencies — `psycopg2-binary` was already in
`requirements.txt`, everything else is the standard library.

## Two events, not one

RazMania sells through two separate Swoogo events with two separate forms, and
nothing joins them:

| | | |
|---|---|---|
| `370376` | *RazMania Midwest Sports & Trading Card Festival* | VIP, Adult, Child, Partners Circle, and the Sponsor a Kid field |
| `372565` | *RazMania Midwest Sports Festival (Exhibitor)* | Front-of-show, Standard and Premium tables, plus extra tables |

Both are swept every run. Adding a third is one entry in `config.py`.

## What counts as a sale

A registrant whose `registration_status` is **confirmed** and whose order total
is **above zero**. Abandoned carts, comped entries and $0 registrations stay
quiet — verified in the tests, not assumed.

**The dollar figure is always Swoogo's own `individual_gross`.** It is never
recomputed from quantity × price, for a specific reason: those two disagree on
the live data. One exhibitor is recorded with 2 Standard tables at $207, the
same price as another vendor's single table. Whatever the reason — a discount, a
later modification — the total Swoogo holds is the one that was actually
charged, and inventing a different number and texting it to three people would
be worse than useless.

The two forms also itemise differently. Ticket dropdowns carry the quantity in
the *choice* (`{"value": "2"}`) and the line item holds a line total; booth
fields are plain numbers (`"8"`) and the line item holds a **unit** price with a
separate quantity. Reading `individual_gross` sidesteps that difference
entirely.

## Credits: read this before it goes live

Textbelt costs **one credit per recipient, per text**. Three phones is three
credits an alert, and this now alerts on *every* sale.

Measured off the live events on 20 Aug 2026:

| | |
|---|---|
| Exhibitor booths | ~40 in the previous 7 days (~6/day), spiking to **14 on 18 Aug** |
| Attendee tickets | ~20 in the previous 24 hours |
| Combined | **~25–30 sales/day, and climbing toward the 29 Aug event** |

At 3 recipients that is **~90 credits a day**. The balance was **697** at setup,
so on current volume it runs dry in **about a week** — and the last week before
an event is exactly when volume climbs. Check it any time with a plain GET that
sends nothing:

```bash
curl https://textbelt.com/quota/$TEXTBELT_KEY
```

**Textbelt returns HTTP 200 with `success: false` when the balance hits zero.**
It looks like a successful send to anything checking only the status code.
`sms.py` checks `success`, logs remaining quota on every send, and warns below
`TEXTBELT_LOW_QUOTA` (default 50). A run where nobody was reached deliberately
does **not** record the sale, so topping up and waiting five minutes delivers
the alerts that were missed rather than losing them.

Three knobs, in order of how much they save:

| | |
|---|---|
| `MIN_ALERT_DOLLARS` | default `0` = text on everything, as asked. Set to `100` and $10 kid tickets are recorded and counted silently while booths and sponsorships still buzz. This is also the difference between ~30 texts a day per phone and ~6. |
| `ALERT_TO` | drop from three numbers to one and the burn drops by two thirds |
| `ALERT_BATCH` | default `1`: several sales inside one poll go out as a single summary text. Helps during a rush, does nothing for a steady trickle. `0` gives one text per sale. |

Lengthening `schedule` does **not** save much on a steady trickle — batching
only helps when two sales land inside the same poll.

## Setup

### 1. Swoogo API credentials — one half still missing

Swoogo → **Account Hub → API → API Keys**. A key comes in two parts, a consumer
key *and* a consumer secret, and both are required: the token endpoint answers

```
401 {"message": "You have provided invalid API details."}
```

to the key on its own — tested against the live account, not assumed.

`SWOOGO_KEY` is set. **`SWOOGO_SECRET` is still blank in `.env`** and the poller
cannot read a single registrant until it is filled. If the secret was only shown
once at creation, delete the key and make a new one; both halves are displayed
together on the new one.

### 2. Textbelt

`TEXTBELT_KEY` is set and was verified live. See the credits section above,
which matters more than the setup does.

**Free fallback.** `SMS_VIA=email` sends to carrier gateways
(`5551234567@vtext.com`, `@tmomail.net`, `@txt.att.net`) using
`SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS`/`SMTP_FROM`. Costs nothing,
guarantees nothing — carriers throttle and silently drop these. Worth having
configured as a bolt-hole for when credits run out at 2am.

### 3. Deploy

The blueprint entry is in `render.yaml` as `razmania-sale-alerts`. Push, then
set the four `sync: false` values in the Render dashboard; they are in local
`.env` too, which is gitignored and stays that way.

### 4. Seed it once — the job will refuse to run until you do

```bash
python3 alerts/watch.py --seed
```

Both events already hold **hundreds of paid registrants**. Seeding records every
one of them and texts nobody. If the state table is empty and the sweep finds
more than `FIRST_RUN_GUARD` (5) sales, the run **aborts with exit 3** and tells
you to seed rather than announcing months of history. Run `--seed` again if the
database is ever rebuilt.

## Testing

```bash
python3 alerts/test_watch.py        # whole alert logic, offline, free
python3 alerts/watch.py --dry-run   # real poll, prints texts, sends none
python3 alerts/watch.py --test      # ONE REAL TEXT to everyone, 3 credits
python3 alerts/watch.py             # for real
```

`test_watch.py` replaces Swoogo and Postgres with in-memory fakes, using field
shapes taken off the live events. It covers a ticket sale, a booth sale naming
the vendor, the same order never texting twice, an upsell, a full refund,
unconfirmed and $0 registrations staying quiet, three sales batching into one
text, a broken API read not being mistaken for a wave of refunds, the unseeded
first-run refusal, and that even an absurdly long vendor name and email still
fit in one credit.

## What it will and will not text about

| | |
|---|---|
| A new ticket, sponsorship or booth order | yes — `new` |
| A buyer adding to an existing order | yes — `increase` |
| An order refunded or reduced | yes — `decrease`, worded as a heads-up |
| A registration that is not `confirmed` | no |
| A $0 registration, or one under `MIN_ALERT_DOLLARS` | recorded and counted, not texted |

The text carries Swoogo's `payment_status` (`Paid`, `Unpaid`, …) rather than
assuming it. A confirmed registration is not proof the card cleared.

## Why you will not get the same text twice

State is written **after** the text goes out, never before. A run that finds the
same total sends nothing. A crash in between re-sends one duplicate next run,
and if no recipient could be reached the total is deliberately left unchanged so
the next run tries again. A duplicate text is a nuisance; a missed sale is not.

```sql
SELECT sent_at, kind, cents_to/100.0 AS dollars, recipient, ok, error
FROM sales_alerts ORDER BY sent_at DESC LIMIT 20;

SELECT kind, count(*) AS orders, sum(cents)/100.0 AS booked
FROM sales_seen GROUP BY kind;
```

That second query is also the honest source for the **kids sponsored** counter
on the Sponsor a Kid page, which is currently a hidden placeholder — filter
`items LIKE '%Sponsor a Kid%'`.

## Known limits

- **Up to five minutes late.** Inherent to polling.
- **Field ids are not stable.** Every `c_XXXXXXX` in `config.py` was read off
  the live forms; rebuilding or cloning a field in Swoogo mints a new id and the
  old one silently stops matching. The failure is graceful — the text still
  names the buyer and the amount, it just cannot say what they bought — but if
  descriptions ever go vague, `config.py` is what drifted.
- **`items` describes, it does not price.** A stale label never produces a wrong
  dollar figure, because the money never comes from that path.
- **Sponsorships are no longer called out specially.** A sponsorship now reads
  as `2 Sponsor a Kid` inside a normal ticket order, because that is literally
  what it is on the form.
- **Messages are ASCII on purpose.** An em dash or an arrow drops SMS from 160
  characters per segment to 70, which on Textbelt is a second credit per alert.
  `trim()` also drops the email rather than tip a long vendor name over 160.
- **One-way.** Textbelt sends from a shared number; nobody can reply.
- **It watches Swoogo, not the bank.** Cash at the door, or a booth invoiced
  outside Swoogo, fires nothing.
