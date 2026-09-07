# Ranking exhibitors across categories, and giving accolades

**Shipped 3 Sep 2026 as "The Best of RazMania 2026"** at
razmania.com/exhibitors/best/ (plugin v1.2.0). Ten lists, ten deep, built by
`exhibitors/rankings.py` from `profiles.json` on every rebuild:

| List | Field | Who is in it |
|---|---|---|
| Break Kings | 9 | live breaks / rips at the table |
| Card Shop All-Stars | 13 | a real storefront or hobby shop |
| Memorabilia Legends | 10 | memorabilia, signings, autographs |
| TCG Trailblazers | 9 | TCG tables outside Pokémon |
| Slab Elite | 11 | slabs, graded singles, grails |
| Trade Table Champions | 18 | trades at the table |
| Top Buyers on the Floor | 23 | buys collections at the show |
| Pokémon Power Tables | 58 | every Pokémon table |
| Michigan's Finest | 37 | based in Michigan |
| Sports Card Heavyweights | 68 | every sports-card table |

Ranks 1–5 carry **Best of RazMania 2026** (gold band on the profile, gold
badge on the directory card); 6–10 carry **RazMania Certified** (ink band and
badge). Membership is from each exhibitor's own chips and category; order is
Instagram audience (largest count from any source, Aug 2026 snapshot), then
profile completeness. Sponsors are never ranked, a list under five members
is not published, and no exhibitor is #1 in more than one list (lists fill
smallest field first; a table already holding a #1 slides to #2). 29
exhibitors hold a Best placing and 24 more a Certified one.

The audience signal is thin (40 of 153 tables have a count), so the lower half
of a list leans on completeness. The sections below are the plan for making
it stronger; the page says the same in its method box.

---

The original proposal follows. The data hooks it describes are what shipped:
every profile carries an `accolades` list and the renderer prints whatever is
in it.

## What a ranking is for

Three audiences, three different reasons to care:

| Who | Wants | So the ranking must be |
|---|---|---|
| **Collectors** deciding which tables to hit first | A short, trustworthy list per interest | Per category, few winners, plain criteria |
| **Exhibitors** deciding whether to come back and what to post | Something to brag about that names them specifically | Many small wins, shareable, on their own page |
| **RazMania** selling tables and tickets | Proof the floor is curated, and a reason for press to link | Defensible numbers, published method, same every year |

The trap is a single "top 10 exhibitors" list. It rewards ten, annoys a
hundred, and a Pokémon vending operation cannot be compared with a vintage
memorabilia dealer on one axis anyway. **Rank inside categories, and give
several kinds of accolade, each with one clear measure.**

## The categories

Use the ones the directory already has, because that is how collectors
already browse: **Sports Trading Cards**, **Pokémon / TCG**, **Memorabilia**,
**Merchandise**. Add two cross-cutting ones that the chips support today:
**Buying tables** (chip `🤝 Buying`) and **Michigan / local** (location chip in
Michigan). Sponsors and partners are never ranked; they are listed.

An exhibitor can hold accolades in more than one category. 614 Rips is a
sports table and a Pokémon table and a buyer; that is three lists it can be
on, and three things it can post.

## Accolade types, and what each one measures

Every accolade has to answer "measured how?" in one line on the page, or it
reads as favouritism. Proposed set, in order of how easy the data is:

1. **Verified Exhibitor** (exists). Confirmed table + reviewed profile. Every
   exhibitor, every year. Not a ranking; the floor.
2. **Most-followed** per category. Instagram followers, snapshotted on a fixed
   date. `exhibitors/data/ig_profiles.json` already holds 45 of these from the
   August scrape. Objective, cheap, and exhibitors love it. Top 3 per category.
3. **Most-viewed profile** per category. Page views on razmania.com over the
   show week, from the site analytics. The Swoogo snippet had a stub for this
   (`TREND_ENDPOINT`, never wired). Top 3 per category. This one rewards the
   exhibitors who actually shared their page, which is exactly the behaviour
   the share kit exists to drive.
4. **Collectors' pick** per category. A vote, the week of the show, one per
   device, run through the `razmania-votes` plugin that is already on the
   site (it does Buy/Pass on stories with a client id and a batched POST; an
   exhibitor vote is the same mechanism with a different subject). Top 1 per
   category, plus "runner-up". The only accolade that is a judgement rather
   than a count, and it is the crowd's, not ours.
5. **Returning exhibitor** / **Founding exhibitor**. From the registrant data
   across years. Free once 2027 exists. Every returning table gets one; it is
   an accolade, not a rank.
6. **Editor's pick**. One per category, chosen by RazMania, with a written
   reason. Keep it to one, and keep the reason on the page.

Do **not** rank on sales, table size, sponsorship level, or anything the
exhibitor pays for. The Verified page promises the stamp "cannot be bought";
the rankings must be able to say the same or the stamp loses its meaning.

## Scoring, kept simple

No composite score. Each accolade is its own list with its own measure, and
the profile shows the ones an exhibitor holds. A composite invites arguments
about weights and produces a number nobody can explain at a table.

Ties go to the exhibitor with the earlier registration. Minimum field: a
category needs 8 ranked exhibitors before it publishes a top 3 (same idea as
the index's n≥20 rule: a "top 3" of four is not a ranking).

## Where it shows

- **Profile hero**: accolade chips under the identity line. Already rendered.
- **Directory**: a sort option ("Most followed", "Most viewed") and a small
  medal on the card. Needs a `rank` field on the card; ten lines of PHP.
- **A rankings page** at `/exhibitors/rankings/`: one section per category,
  each with its lists and the one-line method. This is the press page.
- **Share graphic**: "#1 Pokémon table at RazMania 2026", generated per
  winner, same pipeline as the share kit. Winners post it; that is the
  distribution.

## Data shape

Accolades live in `exhibitors/profiles.overrides.json` under `accolades`,
keyed by Swoogo id, and the build appends them. A separate
`exhibitors/rankings.json` (category → ordered list of ids + the metric value
+ snapshot date) is the audit trail the page cites; a small script turns it
into accolade entries so the two never disagree.

```json
"accolades": {
  "1105601": [
    {"key":"rank","label":"Most-followed Pokémon table","category":"Pokemon / TCG","rank":1,"year":2026,"detail":"28,000 Instagram followers, 20 Aug 2026"}
  ]
}
```

## Order of work

1. **Followers** ranking from the existing scrape: an afternoon, and it gives
   every category a first list.
2. **Page views** once the razmania.com pages have a show week behind them.
3. **Votes** for RazMania 2027, built on `razmania-votes`, opened the week
   before doors.
4. The rankings page and the directory sort, once there are two lists to show.
