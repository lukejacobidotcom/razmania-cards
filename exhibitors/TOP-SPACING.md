# Exhibitors page — top spacing trim

**Live since 25 Aug 2026.** Three rules appended to the CSS pane of snippet
**21022** (`RazMania Exhibitor Directory enhancer`):

```css
.page__header{padding-top:96px!important}
#main-wrap .section{padding-top:8px!important}
.rzd-intro{margin-top:0!important}
```

## What was wrong

`/2026/exhibitors` opened with a 236px band of empty black between the promo bar
and "RAZMANIA 2026". It was three unrelated paddings stacking, none of them
holding any content:

| | |
|---|---|
| `.page__header` | `padding-top: 150px` — from the theme stylesheet, global to every page |
| `#main-wrap .section` | `padding: 60px 0` |
| `.rzd-intro` | `margin: 26px auto` |

The directory snippet hides the stock title and intro widgets
(`#w_110491362{display:none!important}`) and draws its own header, but hiding
those widgets left all the surrounding padding behind.

## Why these numbers

`header.header` is `position: fixed`, height **89px**, sitting at `top: 0` on
this page — the promo bar (67px, in flow) paints over it. `.page__header`'s
padding is the only thing keeping page content clear of that fixed header.

96px keeps the clearance honest on its own — content lands at 181px, clear of
the 89px header by 92px, and still clear by 25px in the case where the header
gets offset below the promo bar (as the homepage script does). Dropping to 72px
tested fine visually but leaves only 1px of margin in that second case, so 96
is the value that ships.

Result: first content 303px → 181px. The search field and the first row of
exhibitor cards are now above the fold.

## Scope

The rules use global selectors but live in a snippet that renders **only on the
exhibitors page**, so no other page is affected. The exhibitor *profile* and
*verified exhibitor* pages use different snippets (21011, 21020) and were not
touched.

## Do not add a block comment to this snippet

Its CSS opens with `/* RazMania exhibitor directory */`. Swoogo merges the first
`/*` with the last `*/` and deletes everything between — a second block comment
would wipe the entire stylesheet. The rules above were appended with no comment
for exactly that reason. See `exit-intent/README.md` for what this bug did the
first time it bit.
