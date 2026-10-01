<?php
/**
 * Plugin Name: RazMania Firsts
 * Description: Firsts — one to three card-market milestones a day, each its own article at /firsts/<id>-<slug>/, plus the series page with the hand-researched all-time firsts. Articles are rendered by the card-data API and hosted here server-side, so Google sees the full text, the NewsArticle markup and the numbers on the first pass.
 * Version:     2.0.0
 * Author:      RazMania
 *
 * A separate plugin, like razmania-index, so nothing here can overwrite the
 * live razmania-cards plugin. Namespaced rzf_ / .rzf / .fx.
 *
 * Two halves that must never blur on the page:
 *   HISTORICAL  data/firsts.json, hand-researched, each entry carrying a status
 *               (verified / reported / open / not_yet). Edit firsts/firsts.json
 *               in the repo, run firsts/build.py, re-upload.
 *   TRACKED     the daily articles and every strip marked "tracked": detected
 *               in RazMania's confirmed eBay sales since 28 Jul 2026, served
 *               by the API (/v1/firsts, /firsts/a/<id>). Never "ever".
 *
 * ROUTES (virtual, like the exhibitors plugin)
 *   /firsts/<id>-<slug>/      one milestone as a news article (API fragment, hosted here)
 *   /firsts/<id>/             same, redirects to the slugged URL
 *   /firsts-sitemap.xml       every published article, for Google News / Top Stories
 * The series page itself is a normal WordPress page at /firsts/ carrying
 * [razmania_firsts]; the rewrite rules only claim paths beneath it.
 */

if (!defined('ABSPATH')) exit;

define('RZF_VERSION',   '2.0.0');
define('RZF_DIR',       plugin_dir_path(__FILE__));
define('RZF_CACHE_TTL', 30 * MINUTE_IN_SECONDS);

/* ------------------------------------------------------------------ config */
function rzf_api_base() {
    foreach (['rzf_api_base', 'rzi_api_base', 'rzm_api_base'] as $k) {
        $v = get_option($k, '');
        if ($v) return rtrim($v, '/');
    }
    return '';
}
function rzf_api_key() {
    foreach (['rzf_api_key', 'rzi_api_key', 'rzm_api_key'] as $k) {
        $v = get_option($k, '');
        if ($v) return $v;
    }
    return '';
}
function rzf_contact()  { return get_option('rzf_contact', ''); }
function rzf_page_url() { return trailingslashit(get_option('rzf_page_url', '') ?: home_url('/firsts/')); }
function rzf_article_url($id, $headline) {
    return rzf_page_url() . (int)$id . '-' . sanitize_title(mb_substr($headline, 0, 80)) . '/';
}

add_action('admin_menu', function () {
    add_options_page('RazMania Firsts', 'RazMania Firsts', 'manage_options', 'rzf', 'rzf_settings_page');
});
add_action('admin_init', function () {
    foreach (['rzf_api_base', 'rzf_api_key', 'rzf_page_url', 'rzf_contact', 'rzf_home_module'] as $k) register_setting('rzf', $k);
    if (!empty($_GET['rzf_flush']) && current_user_can('manage_options')) {
        global $wpdb;
        $wpdb->query("DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient%rzf_%'");
    }
});
function rzf_settings_page() {
    $d = rzf_data(); $s = $d['series'];
    $filled = 0; $open = 0;
    foreach ($d['verticals'] as $v) foreach ($v['firsts'] as $f) {
        if (in_array($f['status'], ['verified', 'reported'], true)) $filled++;
        if ($f['status'] === 'open') $open++;
    }
    $feed = rzf_get('/v1/firsts?days=1');
    $today = (!is_wp_error($feed) && !empty($feed['days'])) ? $feed['days'][0] : null; ?>
    <div class="wrap"><h1>RazMania Firsts</h1>
    <p><strong>Daily:</strong> <?php if ($today): ?>
        <?php echo count($today['items']); ?> published for <?php echo esc_html($today['published_on']); ?> —
        <?php foreach ($today['items'] as $i => $it) echo ($i ? '; ' : '') . '<a href="' . esc_url(rzf_article_url($it['id'], $it['headline'])) . '">' . esc_html($it['headline']) . '</a>'; ?>.
      <?php else: ?>nothing published yet (the API publishes after each daily refresh).<?php endif; ?></p>
    <p><strong>All-time table:</strong> <?php echo (int)$filled; ?> firsts filled, <?php echo (int)$open; ?> open,
       <?php echo count($d['cards']); ?> card profiles, updated <?php echo esc_html($s['updated'] ?? 'unknown'); ?>.
       To add or correct an entry, edit <code>firsts/firsts.json</code> in the repo, run
       <code>python firsts/build.py</code>, and re-upload. Nothing is editable here on purpose.</p>
    <form method="post" action="options.php"><?php settings_fields('rzf'); ?>
      <table class="form-table">
        <tr><th>API base URL</th><td>
          <input type="url" name="rzf_api_base" value="<?php echo esc_attr(get_option('rzf_api_base', '')); ?>" class="regular-text">
          <p class="description">Leave blank to reuse the Index / Cards setting (<?php echo esc_html(rzf_api_base() ?: 'not set'); ?>).</p></td></tr>
        <tr><th>API key</th><td>
          <input type="text" name="rzf_api_key" value="<?php echo esc_attr(get_option('rzf_api_key', '')); ?>" class="regular-text">
          <p class="description">Leave blank to reuse the Index / Cards key.</p></td></tr>
        <tr><th>Series page URL</th><td>
          <input type="url" name="rzf_page_url" value="<?php echo esc_attr(get_option('rzf_page_url', '')); ?>" class="regular-text" placeholder="<?php echo esc_attr(home_url('/firsts/')); ?>">
          <p class="description">The page carrying <code>[razmania_firsts]</code>. Articles live beneath it. Set it once; article URLs are printed in NewsArticle markup and must keep resolving.</p></td></tr>
        <tr><th>Tips address</th><td>
          <input type="email" name="rzf_contact" value="<?php echo esc_attr(rzf_contact()); ?>" class="regular-text" placeholder="firsts@razmania.com">
          <p class="description">Open cells in the all-time table end with "Know the sale? Tell us." — this is where that links.</p></td></tr>
        <tr><th>Homepage module</th><td>
          <select name="rzf_home_module">
            <option value="on" <?php selected(get_option('rzf_home_module', 'on'), 'on'); ?>>On — today's firsts injected at the top of the front page content</option>
            <option value="off" <?php selected(get_option('rzf_home_module', 'on'), 'off'); ?>>Off — place <code>[razmania_firsts_today]</code> by hand</option>
          </select></td></tr>
      </table><?php submit_button(); ?>
      <h2>Shortcodes</h2>
      <pre>[razmania_firsts]                       the series page: today's firsts, the archive, the all-time table, the rules
[razmania_firsts_today]                 today's 1–3 — the homepage module
[razmania_firsts_archive days="30"]     the last N days of published firsts
[razmania_firsts vertical="Baseball"]   one category of the all-time table with its tracked strip
[razmania_firsts_cards]                 the per-card all-time profiles
[razmania_firsts_strip]                 four all-time tiles, one per category</pre>
      <p><a href="<?php echo esc_url(admin_url('options-general.php?page=rzf&rzf_flush=1')); ?>">Clear cached API responses</a>
         · <a href="<?php echo esc_url(home_url('/firsts-sitemap.xml')); ?>">Article sitemap</a></p>
    </form></div>
<?php }

/* -------------------------------------------------------------------- data */
function rzf_data() {
    static $d = null;
    if ($d === null) {
        $raw = @file_get_contents(RZF_DIR . 'data/firsts.json');
        $d = $raw ? json_decode($raw, true) : null;
        if (!is_array($d)) $d = [];
        $d += ['series' => [], 'verticals' => [], 'cards' => []];
    }
    return $d;
}
function rzf_vertical($name) {
    foreach (rzf_data()['verticals'] as $v)
        if (strcasecmp($v['vertical'], $name) === 0 || strcasecmp($v['slug'], $name) === 0) return $v;
    return null;
}

function rzf_get($path, $ttl = RZF_CACHE_TTL) {
    $base = rzf_api_base();
    if (!$base) return new WP_Error('rzf', 'API base URL not set');
    $key = 'rzf_' . md5($base . $path);
    $hit = get_transient($key);
    if ($hit !== false) return $hit;
    $args = ['timeout' => 12, 'headers' => ['Accept' => 'application/json']];
    if ($k = rzf_api_key()) $args['headers']['x-api-key'] = $k;
    $res = wp_remote_get($base . $path, $args);
    $ok  = !is_wp_error($res) && wp_remote_retrieve_response_code($res) === 200;
    if (!$ok) {
        // A first never changes once served, so a stale copy is not wrong, only
        // incomplete. Serve it rather than an error.
        $stale = get_transient($key . '_stale');
        if ($stale !== false) return $stale;
        return is_wp_error($res) ? $res
             : new WP_Error('rzf', 'API returned ' . wp_remote_retrieve_response_code($res));
    }
    $data = json_decode(wp_remote_retrieve_body($res), true);
    set_transient($key, $data, $ttl);
    set_transient($key . '_stale', $data, WEEK_IN_SECONDS);
    return $data;
}

/** The API prints article URLs itself (in "more firsts", "also published"); tell it ours. */
function rzf_site_q() { return '&site=' . rawurlencode(rzf_page_url()); }

/* ----------------------------------------------------------------- helpers */
function rzf_money($n) { return ($n === null || $n === '') ? '—' : '$' . number_format((float)$n); }
function rzf_short($n) {
    $n = (float)$n;
    if ($n >= 1000000) return '$' . rtrim(rtrim(number_format($n / 1000000, 2), '0'), '.') . 'M';
    if ($n >= 1000)    return '$' . rtrim(rtrim(number_format($n / 1000, 1), '0'), '.') . 'K';
    return '$' . number_format($n);
}
function rzf_when($d, $long = false) {
    if (!$d) return '—';
    if (preg_match('/^\d{4}$/', $d)) return $d;
    if (preg_match('/^(\d{4})-(\d{2})$/', $d, $m)) return date_i18n('M Y', mktime(0, 0, 0, (int)$m[2], 1, (int)$m[1]));
    $t = strtotime($d);
    return $t ? date_i18n($long ? 'l j F Y' : 'j M Y', $t) : $d;
}
function rzf_status_label($s) {
    return ['verified' => 'Verified', 'reported' => 'Reported', 'open' => 'Open', 'not_yet' => 'Not yet'][$s] ?? $s;
}
function rzf_kind_label($k) {
    static $m = ['price:vertical' => 'Price milestone', 'price:subject' => 'Price milestone', 'price:card' => 'Card milestone',
        'price:card_grade' => 'Card milestone', 'price:set' => 'Set milestone', 'price:year' => 'Release milestone',
        'price:year_rookie' => 'Rookie milestone', 'count:day' => 'Volume milestone', 'count:week' => 'Volume milestone',
        'count:total' => 'Cumulative milestone', 'gmv:day' => 'Volume milestone', 'gmv:week' => 'Volume milestone',
        'gmv:total' => 'Cumulative milestone', 'index:above' => 'Index milestone', 'index:below' => 'Index milestone'];
    return $m[$k] ?? 'Milestone';
}
function rzf_vlabel($v) { return $v === 'Pokemon' ? 'Pokémon' : ($v === 'All' ? 'The hobby' : $v); }
function rzf_err($e) {
    return '<p class="rzf-muted">Live tracking is temporarily unavailable.<!-- '
         . esc_html(is_wp_error($e) ? $e->get_error_message() : 'no data') . ' --></p>';
}

/* ------------------------------------------------------------ daily blocks */
/** One published first as a card. */
function rzf_feed_card($x) {
    $img = !empty($x['image_url'])
        ? '<img src="' . esc_url($x['image_url']) . '" alt="" loading="lazy">'
        : '<div class="fx-noimg">' . esc_html(strpos($x['kind'], 'count') === 0 ? number_format((float)$x['threshold']) : rzf_short($x['threshold'])) . '</div>';
    return '<a class="fx-card" href="' . esc_url(rzf_article_url($x['id'], $x['headline'])) . '">' . $img
         . '<div><span class="fx-eyebrow">' . esc_html(rzf_vlabel($x['vertical'])) . ' · ' . esc_html(rzf_kind_label($x['kind'])) . '</span>'
         . '<h3>' . esc_html($x['headline']) . '</h3><p>Settled ' . esc_html(rzf_when($x['first_date'])) . '</p></div></a>';
}

function rzf_today_block($heading = true) {
    $f = rzf_get('/v1/firsts?days=1');
    if (is_wp_error($f)) return '<div class="rzf">' . rzf_err($f) . '</div>';
    if (empty($f['days'])) return '<div class="rzf"><p class="rzf-muted">Firsts publishes after the next daily refresh.</p></div>';
    $day = $f['days'][0];
    ob_start(); ?>
    <section class="rzf fx fx-today-wrap">
      <?php if ($heading): ?>
      <div class="fx-kicker"><span class="fx-eyebrow">Firsts · <?php echo esc_html(rzf_when($day['published_on'], true)); ?></span>
        <span><a href="<?php echo esc_url(rzf_page_url()); ?>">The series →</a></span></div>
      <?php endif; ?>
      <div class="fx-today"><?php foreach ($day['items'] as $x) echo rzf_feed_card($x); ?></div>
    </section>
    <?php return ob_get_clean();
}

function rzf_archive_block($days = 30) {
    $f = rzf_get('/v1/firsts?days=' . (int)$days);
    if (is_wp_error($f)) return rzf_err($f);
    if (empty($f['days'])) return '';
    ob_start(); ?>
    <section class="rzf-archive" id="archive">
      <div class="rzf-head"><h2 class="rzf-h2">The last <?php echo (int)$days; ?> days</h2>
        <p class="rzf-sub">Every first published, newest day first. Tracked sales over <?php echo esc_html(rzf_money($f['floor'] ?? 2000)); ?> since <?php echo esc_html(rzf_when($f['tracked_since'] ?? '')); ?>.</p></div>
      <?php foreach ($f['days'] as $d): ?>
      <div class="rzf-day"><h3><?php echo esc_html(rzf_when($d['published_on'], true)); ?></h3>
        <div class="fx-today"><?php foreach ($d['items'] as $x) echo rzf_feed_card($x); ?></div></div>
      <?php endforeach; ?>
    </section>
    <?php return ob_get_clean();
}

/* ------------------------------------------------------- all-time blocks */
function rzf_cell($f, $record, $compact = false) {
    $st = $f['status']; $thr = (float)$f['threshold'];
    ob_start(); ?>
    <div class="rzf-cell rzf-cell--<?php echo esc_attr($st); ?>">
      <div class="rzf-cell-top">
        <span class="rzf-line"><?php echo esc_html(rzf_short($thr)); ?></span>
        <span class="rzf-badge rzf-badge--<?php echo esc_attr($st); ?>"><?php echo esc_html(rzf_status_label($st)); ?></span>
      </div>
      <?php if ($st === 'verified' || $st === 'reported'): ?>
        <p class="rzf-card"><?php echo esc_html($f['card']); ?></p>
        <p class="rzf-price"><?php echo esc_html(rzf_money($f['price'])); ?></p>
        <p class="rzf-meta"><?php echo esc_html(rzf_when($f['date'])); ?><?php
          if (!empty($f['venue'])) echo ' · ' . esc_html($f['venue']);
          if (!empty($f['buyer'])) echo ' · to ' . esc_html($f['buyer']); ?></p>
        <?php if (!$compact && !empty($f['note'])): ?><p class="rzf-note"><?php echo esc_html($f['note']); ?></p><?php endif; ?>
        <?php if (!$compact && !empty($f['sources'])): ?>
          <p class="rzf-src">Sources: <?php
            $out = [];
            foreach ($f['sources'] as $s) {
                $out[] = !empty($s['url'])
                    ? '<a href="' . esc_url($s['url']) . '" rel="nofollow noopener" target="_blank">' . esc_html($s['name']) . '</a>'
                    : esc_html($s['name']);
            }
            echo implode('; ', $out); ?></p>
        <?php endif; ?>
      <?php elseif ($st === 'open'): ?>
        <p class="rzf-card rzf-card--quiet">Not yet sourced.</p>
        <?php if (!empty($f['note'])): ?><p class="rzf-note"><?php echo esc_html($f['note']); ?></p><?php endif; ?>
        <p class="rzf-ask">Know the sale?
          <?php if ($c = rzf_contact()): ?><a href="mailto:<?php echo esc_attr($c); ?>?subject=<?php echo rawurlencode('Firsts: ' . rzf_short($thr)); ?>">Tell us.</a><?php else: ?>Tell us.<?php endif; ?></p>
      <?php else: ?>
        <p class="rzf-card">Nobody. Yet.</p>
        <?php if ($record && !empty($record['card'])): ?>
          <p class="rzf-note">Closest so far: <?php echo esc_html($record['card']); ?> —
            <?php echo esc_html(rzf_money($record['price'])); ?>, <?php echo esc_html(rzf_when($record['date'])); ?><?php
            if (!empty($record['venue'])) echo ', ' . esc_html($record['venue']); ?>.</p>
        <?php endif; ?>
      <?php endif; ?>
    </div>
    <?php return ob_get_clean();
}

/** The tracked strip under a category: its own price ladder and biggest-name firsts, from the log. */
function rzf_tracked_block($vertical) {
    $d = rzf_get('/v1/firsts/log?vertical=' . rawurlencode($vertical) . '&kind=price:vertical,price:subject&limit=400');
    if (is_wp_error($d)) return '<div class="rzf-tracked">' . rzf_err($d) . '</div>';
    $lines = []; $subs = [];
    foreach ((array)($d['firsts'] ?? []) as $r) {
        if ($r['kind'] === 'price:vertical') $lines[] = $r;
        elseif (!isset($subs[$r['subject']]) || (float)$r['threshold'] > (float)$subs[$r['subject']]['threshold']) $subs[$r['subject']] = $r;
    }
    usort($lines, function ($a, $b) { return (float)$a['threshold'] <=> (float)$b['threshold']; });
    usort($subs, function ($a, $b) {
        if ((float)$a['threshold'] !== (float)$b['threshold']) return (float)$b['threshold'] <=> (float)$a['threshold'];
        return strcmp($a['first_date'], $b['first_date']);
    });
    $subs = array_slice($subs, 0, 6);
    ob_start(); ?>
    <div class="rzf-tracked">
      <span class="rzf-eyebrow">First tracked by RazMania</span>
      <p class="rzf-tracked-sub">Confirmed eBay sales over $2,000, best-offer listings excluded, recorded since 28 Jul 2026.
        These are RazMania's firsts, not the hobby's — that is the table above. Each links to its article.</p>
      <?php if (!$lines): ?><p class="rzf-muted">No settled sale over $2,500 tracked in this category yet.</p><?php else: ?>
      <ul class="rzf-tape">
        <?php foreach ($lines as $r): ?>
        <li><span class="rzf-t-line"><?php echo esc_html(rzf_short($r['threshold'])); ?></span>
            <a href="<?php echo esc_url(rzf_article_url($r['id'], $r['headline'])); ?>"><?php echo esc_html($r['title'] ?: $r['headline']); ?></a>
            <strong><?php echo esc_html(rzf_money($r['value'])); ?></strong>
            <em><?php echo esc_html(rzf_when($r['first_date'])); ?></em></li>
        <?php endforeach; ?>
      </ul>
      <?php endif; if ($subs): ?>
      <ul class="rzf-subs">
        <?php foreach ($subs as $r): ?>
        <li><span class="rzf-sub-name"><?php echo esc_html($r['headline']); ?></span>
            <a href="<?php echo esc_url(rzf_article_url($r['id'], $r['headline'])); ?>"><?php echo esc_html($r['title']); ?></a>
            <strong><?php echo esc_html(rzf_money($r['value'])); ?></strong>
            <em><?php echo esc_html(rzf_when($r['first_date'])); ?></em></li>
        <?php endforeach; ?>
      </ul>
      <?php endif; ?>
    </div>
    <?php return ob_get_clean();
}

function rzf_vertical_section($v) {
    ob_start(); ?>
    <section class="rzf-vert" id="firsts-<?php echo esc_attr($v['slug']); ?>">
      <div class="rzf-head">
        <h2 class="rzf-h2"><?php echo esc_html($v['label']); ?></h2>
        <p class="rzf-sub"><?php echo esc_html($v['universe']); ?></p>
      </div>
      <div class="rzf-grid"><?php foreach ($v['firsts'] as $f) echo rzf_cell($f, $v['record'] ?? null); ?></div>
      <?php echo rzf_tracked_block($v['vertical']); ?>
    </section>
    <?php return ob_get_clean();
}

function rzf_cards_section() {
    $cards = rzf_data()['cards'];
    if (!$cards) return '';
    ob_start(); ?>
    <section class="rzf-cards" id="firsts-cards">
      <div class="rzf-head"><h2 class="rzf-h2">One card at a time</h2>
        <p class="rzf-sub">The same four lines, for a single card. More are added as they are sourced.</p></div>
      <?php foreach ($cards as $c): ?>
      <div class="rzf-cardrow" id="firsts-<?php echo esc_attr(sanitize_title($c['subject'])); ?>">
        <h3><?php echo esc_html($c['card']); ?> <span class="rzf-muted">· <?php echo esc_html(rzf_vlabel($c['vertical'])); ?></span></h3>
        <div class="rzf-grid"><?php foreach ($c['firsts'] as $f) echo rzf_cell($f, $c['record'] ?? null, true); ?></div>
      </div>
      <?php endforeach; ?>
    </section>
    <?php return ob_get_clean();
}

function rzf_method() {
    $s = rzf_data()['series'];
    ob_start(); ?>
    <section class="rzf-method" id="how-a-first-is-decided">
      <span class="rzf-eyebrow">The rules</span>
      <h2 class="rzf-h2">How a first is decided</h2>
      <ol class="rzf-rules"><?php foreach ((array)($s['rules'] ?? []) as $r): ?><li><?php echo esc_html($r); ?></li><?php endforeach; ?></ol>
      <dl class="rzf-legend">
        <?php foreach ((array)($s['statuses'] ?? []) as $k => $txt): ?>
        <div><dt><span class="rzf-badge rzf-badge--<?php echo esc_attr($k); ?>"><?php echo esc_html(rzf_status_label($k)); ?></span></dt><dd><?php echo esc_html($txt); ?></dd></div>
        <?php endforeach; ?>
      </dl>
      <?php if (!empty($s['universe_note'])): ?><p class="rzf-note"><?php echo esc_html($s['universe_note']); ?></p><?php endif; ?>
      <p class="rzf-asof">All-time table updated <?php echo esc_html(rzf_when($s['updated'] ?? '')); ?>. Daily firsts publish after each refresh.</p>
    </section>
    <?php return ob_get_clean();
}

function rzf_top_first($v) {
    $top = null;
    foreach ($v['firsts'] as $f) if (in_array($f['status'], ['verified', 'reported'], true)) $top = $f;
    return $top;
}

/* -------------------------------------------------------------- shortcodes */
add_shortcode('razmania_firsts', function ($a) {
    $a = shortcode_atts(['vertical' => ''], $a);
    if ($a['vertical']) {
        $v = rzf_vertical($a['vertical']);
        return $v ? '<div class="rzf">' . rzf_vertical_section($v) . '</div>' : '<p class="rzf rzf-muted">Unknown category.</p>';
    }
    $d = rzf_data(); $s = $d['series'];
    ob_start(); ?>
    <article class="rzf rzf-page">
      <header class="rzf-mast">
        <span class="rzf-eyebrow">A RazMania series</span>
        <h1 class="rzf-h1"><?php echo esc_html($s['name'] ?? 'Firsts'); ?></h1>
        <p class="rzf-tag">One to three card-market milestones a day, the first time the market does something. Plus the hobby's all-time firsts, researched by hand.</p>
        <p class="rzf-dek"><?php echo esc_html($s['dek'] ?? ''); ?></p>
      </header>
      <?php echo rzf_today_block(true); ?>
      <?php echo rzf_archive_block(14); ?>
      <section class="rzf-alltime" id="all-time">
        <div class="rzf-head"><h2 class="rzf-h2">The all-time firsts</h2>
          <p class="rzf-sub"><?php echo esc_html($s['tagline'] ?? ''); ?></p></div>
        <?php echo do_shortcode('[razmania_firsts_strip]'); ?>
      </section>
      <?php foreach ($d['verticals'] as $v) echo rzf_vertical_section($v); ?>
      <?php echo rzf_cards_section(); ?>
      <?php echo rzf_method(); ?>
    </article>
    <?php return ob_get_clean();
});
add_shortcode('razmania_firsts_today', function () { return rzf_today_block(true); });
add_shortcode('razmania_firsts_archive', function ($a) {
    $a = shortcode_atts(['days' => 30], $a);
    return '<div class="rzf">' . rzf_archive_block((int)$a['days']) . '</div>';
});
add_shortcode('razmania_firsts_cards', function () { return '<div class="rzf">' . rzf_cards_section() . '</div>'; });
add_shortcode('razmania_firsts_strip', function () {
    $d = rzf_data(); $url = rzf_page_url();
    ob_start(); ?>
    <div class="rzf rzf-tiles">
      <?php foreach ($d['verticals'] as $v): $top = rzf_top_first($v); ?>
      <a class="rzf-tile" href="<?php echo esc_url($url . '#firsts-' . $v['slug']); ?>">
        <span class="rzf-cat"><?php echo esc_html($v['label']); ?></span>
        <?php if ($top): ?>
          <span class="rzf-tile-val"><?php echo esc_html(rzf_short($top['threshold'])); ?></span>
          <span class="rzf-tile-sub">first crossed <?php echo esc_html(rzf_when($top['date'])); ?><br><?php echo esc_html($top['card']); ?></span>
        <?php else: ?>
          <span class="rzf-tile-val rzf-tile-val--quiet">—</span><span class="rzf-tile-sub">Being researched</span>
        <?php endif; ?>
      </a>
      <?php endforeach; ?>
    </div>
    <?php return ob_get_clean();
});

/* ---------------------------------------------------------- homepage module */
add_filter('the_content', function ($content) {
    if (get_option('rzf_home_module', 'on') !== 'on') return $content;
    if (!is_front_page() || !in_the_loop() || !is_main_query()) return $content;
    return rzf_today_block(true) . $content;
}, 5);

/* ----------------------------------------------------------------- routing */
function rzf_add_rules() {
    add_rewrite_rule('^firsts/(\d+)-([a-z0-9\-]+)/?$', 'index.php?rzf=article&rzf_id=$matches[1]&rzf_slug=$matches[2]', 'top');
    add_rewrite_rule('^firsts/(\d+)/?$', 'index.php?rzf=article&rzf_id=$matches[1]', 'top');
    add_rewrite_rule('^firsts-sitemap\.xml$', 'index.php?rzf=sitemap', 'top');
}
add_action('init', 'rzf_add_rules');
add_filter('query_vars', function ($v) { $v[] = 'rzf'; $v[] = 'rzf_id'; $v[] = 'rzf_slug'; return $v; });
register_activation_hook(__FILE__, function () { rzf_add_rules(); flush_rewrite_rules(); update_option('rzf_rules_version', RZF_VERSION); });
register_deactivation_hook(__FILE__, 'flush_rewrite_rules');
add_action('wp_loaded', function () {
    if (get_option('rzf_rules_version') !== RZF_VERSION) { flush_rewrite_rules(false); update_option('rzf_rules_version', RZF_VERSION); }
});

add_action('template_redirect', function () {
    $route = get_query_var('rzf');
    if (!$route) return;
    global $wp_query;
    if ($route === 'sitemap') { rzf_render_sitemap(); exit; }
    $id = (int)get_query_var('rzf_id');
    if (!$id) return;
    // The article comes from the API as a fragment plus meta; a day's cache,
    // because a first never changes once published.
    $a = rzf_get('/firsts/a/' . $id . '?fragment=1' . rzf_site_q(), DAY_IN_SECONDS);
    if (is_wp_error($a) || empty($a['html'])) { $wp_query->set_404(); status_header(404); nocache_headers(); return; }
    $meta = $a['meta'];
    $canon = rzf_article_url($id, $meta['title']);
    if (trailingslashit(home_url(add_query_arg([], $GLOBALS['wp']->request))) !== $canon) { wp_redirect($canon, 301); exit; }

    status_header(200);
    $wp_query->is_404 = false;
    if (!headers_sent()) header('Cache-Control: public, max-age=3600, s-maxage=86400');
    rzf_strip_seo_head();
    $GLOBALS['rzf_article'] = $a;
    $GLOBALS['rzf_canon'] = $canon;
    add_filter('pre_get_document_title', function () use ($meta) { return $meta['title'] . ' — RazMania Firsts'; }, PHP_INT_MAX);
    add_action('wp_head', 'rzf_head_meta', 1);
    add_filter('body_class', function ($c) { return array_merge($c, ['rzf-route', 'rzf-article', 'x-full-width-layout-active', 'x-full-width-active']); });
    get_header();
    echo '<div class="x-container max width offset"><div class="x-main full" role="main"><div class="rzf rzf-article-wrap">';
    echo '<p class="rzf-crumb"><a href="' . esc_url(home_url('/')) . '">RazMania</a> › <a href="' . esc_url(rzf_page_url()) . '">Firsts</a></p>';
    echo $a['html'];   // server-rendered by our own API; already escaped there
    echo '</div></div></div>';
    get_footer();
    exit;
}, 0);

function rzf_head_meta() {
    $a = $GLOBALS['rzf_article']; $m = $a['meta']; $canon = $GLOBALS['rzf_canon'];
    $title = $m['title'] . ' — RazMania Firsts';
    printf('<title>%s</title>' . "\n", esc_html($title));
    printf('<link rel="canonical" href="%s">' . "\n", esc_url($canon));
    printf('<meta name="description" content="%s">' . "\n", esc_attr($m['description']));
    printf('<meta property="og:type" content="article"><meta property="og:title" content="%s"><meta property="og:description" content="%s"><meta property="og:url" content="%s">' . "\n",
           esc_attr($m['title']), esc_attr($m['description']), esc_url($canon));
    if (!empty($m['image'])) printf('<meta property="og:image" content="%s"><meta name="twitter:card" content="summary_large_image">' . "\n", esc_url($m['image']));
    if (!empty($m['published_on'])) printf('<meta property="article:published_time" content="%sT09:00:00Z"><meta property="article:section" content="%s">' . "\n",
           esc_attr($m['published_on']), esc_attr(rzf_vlabel($m['vertical'])));
    foreach ((array)($m['jsonld'] ?? []) as $ld) {
        // The API printed its own URL scheme; the canonical is ours.
        if (($ld['@type'] ?? '') === 'NewsArticle') { $ld['url'] = $canon; $ld['mainEntityOfPage'] = $canon; }
        echo '<script type="application/ld+json">' . wp_json_encode($ld, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . '</script>' . "\n";
    }
    echo '<style id="rzf-article-css">' . $a['css'] . '</style>' . "\n";
}

/** Same reason as the exhibitors plugin: these URLs resolve as the blog index, so
 *  Rank Math would print the News page's title and Open Graph first. */
function rzf_strip_seo_head() {
    global $wp_filter;
    if (empty($wp_filter['wp_head'])) return;
    foreach ($wp_filter['wp_head']->callbacks as $priority => $callbacks) {
        foreach ($callbacks as $cb) {
            $fn = $cb['function'];
            if (is_array($fn) && isset($fn[0]) && is_object($fn[0]) && stripos(get_class($fn[0]), 'RankMath') !== false) {
                remove_action('wp_head', $fn, $priority);
            }
        }
    }
    remove_action('wp_head', 'rel_canonical');
    remove_action('wp_head', 'wp_shortlink_wp_head', 10);
    remove_action('wp_head', '_wp_render_title_tag', 1);
}

/** Every published article, newest first, with news:news tags for the last two days. */
function rzf_render_sitemap() {
    status_header(200);
    header('Content-Type: application/xml; charset=UTF-8');
    header('Cache-Control: public, max-age=3600');
    $f = rzf_get('/v1/firsts?days=365', HOUR_IN_SECONDS);
    echo '<' . '?xml version="1.0" encoding="UTF-8"?' . '>' . "\n";
    echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">' . "\n";
    echo '<url><loc>' . esc_url(rzf_page_url()) . '</loc><changefreq>daily</changefreq><priority>0.9</priority></url>' . "\n";
    if (!is_wp_error($f)) {
        $recent = strtotime('-2 days');
        foreach ((array)($f['days'] ?? []) as $d) foreach ($d['items'] as $x) {
            echo '<url><loc>' . esc_url(rzf_article_url($x['id'], $x['headline'])) . '</loc><lastmod>' . esc_html($d['published_on']) . '</lastmod>';
            if (strtotime($d['published_on']) >= $recent) {
                echo '<news:news><news:publication><news:name>RazMania</news:name><news:language>en</news:language></news:publication>'
                   . '<news:publication_date>' . esc_html($d['published_on']) . 'T09:00:00Z</news:publication_date>'
                   . '<news:title>' . esc_html($x['headline']) . '</news:title></news:news>';
            }
            if (!empty($x['image_url'])) echo '<image:image><image:loc>' . esc_url($x['image_url']) . '</image:loc></image:image>';
            echo "</url>\n";
        }
    }
    echo '</urlset>';
}
add_filter('robots_txt', function ($out) { return rtrim($out) . "\nSitemap: " . home_url('/firsts-sitemap.xml') . "\n"; });

/* ------------------------------------------------------------------ styles */
add_action('wp_enqueue_scripts', function () {
    wp_register_style('rzf', false, [], RZF_VERSION);
    wp_enqueue_style('rzf');
    // The feed cards share the article's .fx classes; the article page gets the
    // full sheet from the API in rzf_head_meta(). This is the subset the series
    // page and homepage module need, plus the all-time table.
    wp_add_inline_style('rzf', '
    .rzf{--rzf-ink:var(--ink,#14110D);--rzf-ink2:var(--ink2,#57514A);--rzf-ink3:var(--ink3,#6E6862);--rzf-bg:var(--bg,#FBF9F5);--rzf-s1:var(--s1,#fff);--rzf-s2:var(--s2,#F2EDE4);--rzf-line:var(--line,rgba(26,22,16,.11));--rzf-line2:var(--line2,rgba(26,22,16,.2));--rzf-gold:var(--gold,#9A6B00);
         color:var(--rzf-ink);font-variant-numeric:tabular-nums;line-height:1.5}
    .rzf *{box-sizing:border-box}
    .rzf-muted{color:var(--rzf-ink3);font-size:13px}
    .rzf-eyebrow,.rzf .fx-eyebrow{display:block;font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--rzf-gold)}
    .rzf-h1,.rzf-h2,.rzf h3{color:var(--rzf-ink)}
    .rzf-h1{font-family:Georgia,"Times New Roman",serif;font-weight:700;font-size:clamp(48px,8vw,92px);line-height:.95;letter-spacing:-.03em;margin:10px 0 14px}
    .rzf-h2{font-family:Georgia,"Times New Roman",serif;font-size:clamp(26px,3.2vw,36px);line-height:1.1;margin:0}
    .rzf-tag{font-family:Georgia,serif;font-size:clamp(19px,2.4vw,26px);line-height:1.3;margin:0 0 10px;max-width:44ch}
    .rzf-dek{font-size:clamp(16px,1.8vw,19px);line-height:1.5;color:var(--rzf-ink2);max-width:64ch;margin:0 0 26px}
    .rzf-mast{padding:34px 0 26px;border-bottom:1px solid var(--rzf-line2)}
    .rzf-crumb{font-size:13px;color:var(--rzf-ink3);margin:18px 0 0}.rzf-crumb a{color:inherit;text-decoration:none}
    /* feed cards (shared with the article page) */
    .rzf .fx-kicker{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline;margin:26px 0 12px}
    .rzf .fx-kicker .fx-eyebrow{display:inline}.rzf .fx-kicker a{color:var(--rzf-ink);text-decoration:none;font-weight:700;border-bottom:2px solid var(--rzf-gold);font-size:13px}
    .rzf .fx-today{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin:0 0 10px}
    .rzf .fx-card{display:grid;grid-template-columns:72px 1fr;gap:12px;align-items:start;background:var(--rzf-s1);border:1px solid var(--rzf-line);border-radius:12px;padding:14px 16px;text-decoration:none;color:inherit}
    .rzf .fx-card:hover{border-color:var(--rzf-line2)}
    .rzf .fx-card img{width:72px;height:72px;object-fit:cover;border-radius:8px;background:var(--rzf-s2)}
    .rzf .fx-card .fx-noimg{width:72px;height:72px;border-radius:8px;background:var(--rzf-ink);color:#F5C518;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:15px}
    .rzf .fx-card h3{font-family:Georgia,serif;font-size:18px;line-height:1.25;margin:4px 0 6px;color:var(--rzf-ink)}
    .rzf .fx-card p{margin:0;font-size:13px;color:var(--rzf-ink3)}
    .rzf-archive{padding:30px 0 8px;border-bottom:1px solid var(--rzf-line)}
    .rzf-day{margin:18px 0 6px}.rzf-day h3{font-family:Georgia,serif;font-size:17px;margin:0 0 10px;color:var(--rzf-ink2)}
    .rzf-alltime{padding:36px 0 10px}
    /* tiles */
    .rzf-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:16px}
    .rzf-tile{display:flex;flex-direction:column;gap:2px;background:var(--rzf-s1);border:1px solid var(--rzf-line);border-radius:12px;padding:16px 18px;text-decoration:none;color:inherit}
    .rzf-tile:hover{border-color:var(--rzf-line2)}
    .rzf-cat{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--rzf-ink3)}
    .rzf-tile-val{font-size:clamp(38px,4.5vw,52px);font-weight:800;line-height:1;letter-spacing:-.02em}
    .rzf-tile-val--quiet{color:var(--rzf-ink3)}
    .rzf-tile-sub{font-size:12px;color:var(--rzf-ink3);line-height:1.4}
    /* a category */
    .rzf-vert{padding:36px 0 8px;border-bottom:1px solid var(--rzf-line)}
    .rzf-head{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;margin-bottom:16px}
    .rzf-sub{margin:0;font-size:13px;color:var(--rzf-ink3)}
    .rzf-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
    @media(max-width:900px){.rzf-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:480px){.rzf-grid{grid-template-columns:1fr}}
    .rzf-cell{background:var(--rzf-s1);border:1px solid var(--rzf-line);border-radius:12px;padding:16px 18px 14px;display:flex;flex-direction:column;gap:4px;min-height:200px}
    .rzf-cell--verified,.rzf-cell--reported{border-top:3px solid var(--rzf-gold)}
    .rzf-cell--open{border-style:dashed}
    .rzf-cell--not_yet{background:var(--rzf-s2)}
    .rzf-cell-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
    .rzf-line{font-size:30px;font-weight:800;letter-spacing:-.02em;line-height:1}
    .rzf-badge{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:3px 7px;border-radius:999px;border:1px solid var(--rzf-line2);color:var(--rzf-ink2);white-space:nowrap}
    .rzf-badge--verified{background:var(--rzf-gold);border-color:var(--rzf-gold);color:#fff}
    .rzf-badge--reported{border-color:var(--rzf-gold);color:var(--rzf-gold)}
    .rzf-badge--not_yet{color:var(--rzf-ink3)}
    .rzf-card{font-family:Georgia,serif;font-size:16px;line-height:1.3;margin:8px 0 0;font-weight:700}
    .rzf-card--quiet{font-weight:400;color:var(--rzf-ink3)}
    .rzf-price{font-size:24px;font-weight:800;letter-spacing:-.02em;margin:2px 0 0}
    .rzf-meta{font-size:13px;color:var(--rzf-ink2);margin:0}
    .rzf-note{font-size:13px;line-height:1.5;color:var(--rzf-ink2);margin:8px 0 0}
    .rzf-src{font-size:11.5px;color:var(--rzf-ink3);margin:auto 0 0;padding-top:8px}.rzf-src a{color:inherit}
    .rzf-ask{font-size:13px;margin:auto 0 0;padding-top:8px;font-weight:700}
    .rzf-ask a{color:var(--rzf-ink);border-bottom:2px solid var(--rzf-gold);text-decoration:none}
    /* tracked strip */
    .rzf-tracked{margin:18px 0 22px;padding:14px 18px;background:var(--rzf-ink);color:#f1ede6;border-radius:10px}
    .rzf-tracked .rzf-eyebrow{color:#F5C518}
    .rzf-tracked-sub{font-size:12.5px;color:#a8a199;margin:4px 0 10px;max-width:80ch}
    .rzf-tracked .rzf-muted{color:#a8a199}
    .rzf-tape,.rzf-subs{list-style:none;margin:0;padding:0}
    .rzf-tape li,.rzf-subs li{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;padding:7px 0;border-top:1px solid rgba(255,255,255,.1);font-size:13.5px}
    .rzf-tape li:first-child{border-top:0}
    .rzf-subs{margin-top:10px;border-top:1px dashed rgba(255,255,255,.2);padding-top:4px}
    .rzf-t-line{flex:0 0 58px;font-weight:800;color:#F5C518;font-size:15px}
    .rzf-sub-name{flex:0 0 auto;font-weight:700;color:#F5C518}
    .rzf-tape a,.rzf-subs a{color:#f1ede6;text-decoration:none;flex:1 1 320px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .rzf-tape a:hover,.rzf-subs a:hover{text-decoration:underline}
    .rzf-tape strong,.rzf-subs strong{color:#fff}
    .rzf-tape em,.rzf-subs em{font-style:normal;color:#a8a199;font-size:12px}
    /* cards */
    .rzf-cards{padding:36px 0 8px;border-bottom:1px solid var(--rzf-line)}
    .rzf-cardrow{margin:18px 0 26px}
    .rzf-cardrow h3{font-family:Georgia,serif;font-size:20px;margin:0 0 10px}
    .rzf-cardrow .rzf-cell{min-height:150px}
    /* rules */
    .rzf-method{padding:36px 0 20px;max-width:78ch}
    .rzf-rules{padding-left:22px;margin:14px 0 18px}
    .rzf-rules li{font-size:15.5px;line-height:1.6;margin:0 0 10px}
    .rzf-legend{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:0 0 14px}
    .rzf-legend>div{background:var(--rzf-s1);border:1px solid var(--rzf-line);border-radius:8px;padding:10px 12px}
    .rzf-legend dt{margin:0 0 4px}.rzf-legend dd{margin:0;font-size:13px;color:var(--rzf-ink2)}
    .rzf-asof{font-size:13px;color:var(--rzf-ink3)}
    ');
});
