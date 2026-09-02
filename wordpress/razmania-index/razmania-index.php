<?php
/**
 * Plugin Name: RazMania Index
 * Description: The RazMania Index — a daily, methodology-backed measure of the trading-card market, built from confirmed eBay sales with best-offer listings excluded. Renders entirely server-side.
 * Version:     1.0.0
 * Author:      RazMania
 *
 * This is a SEPARATE plugin from razmania-cards, on purpose. The production
 * copy of razmania-cards has grown a design system, email capture and theme
 * integration that do not live in this repository, so shipping the index as a
 * patch to that file would mean overwriting work we cannot see. Everything
 * here is namespaced rzi_ / .rzi and depends on nothing but the API.
 *
 * It borrows the live site's design tokens (--bg, --ink, --line, --gold, --up,
 * --down …) with fallbacks, so it looks native on razmania.com and still
 * renders correctly anywhere else.
 */

if (!defined('ABSPATH')) exit;

define('RZI_VERSION',    '1.0.0');
define('RZI_METHOD_VER', '1.0');            // bump when the METHODOLOGY changes
define('RZI_CACHE_TTL',  30 * MINUTE_IN_SECONDS);

/* ------------------------------------------------------------------ config */
/**
 * Reuses the razmania-cards API settings when they exist, so installing this
 * plugin on the live site needs zero configuration. Own settings override.
 */
function rzi_api_base() {
    $v = get_option('rzi_api_base', '');
    if (!$v) $v = get_option('rzm_api_base', '');
    return rtrim($v, '/');
}
function rzi_api_key() {
    $v = get_option('rzi_api_key', '');
    if (!$v) $v = get_option('rzm_api_key', '');
    return $v;
}

add_action('admin_menu', function () {
    add_options_page('RazMania Index', 'RazMania Index', 'manage_options', 'rzi', 'rzi_settings_page');
});
add_action('admin_init', function () {
    register_setting('rzi', 'rzi_api_base');
    register_setting('rzi', 'rzi_api_key');
    register_setting('rzi', 'rzi_page_url');
    if (!empty($_GET['rzi_flush']) && current_user_can('manage_options')) {
        global $wpdb;
        $wpdb->query("DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient%rzi_%'");
    }
});
function rzi_settings_page() { ?>
    <div class="wrap"><h1>RazMania Index</h1>
    <form method="post" action="options.php"><?php settings_fields('rzi'); ?>
      <table class="form-table">
        <tr><th>API base URL</th><td>
          <input type="url" name="rzi_api_base" value="<?php echo esc_attr(get_option('rzi_api_base', '')); ?>" class="regular-text"
                 placeholder="<?php echo esc_attr(get_option('rzm_api_base', 'https://razmania-cards-api-t68v.onrender.com')); ?>">
          <p class="description">Leave blank to reuse the RazMania Cards setting (<?php echo esc_html(rzi_api_base() ?: 'not set'); ?>).</p></td></tr>
        <tr><th>API key</th><td>
          <input type="text" name="rzi_api_key" value="<?php echo esc_attr(get_option('rzi_api_key', '')); ?>" class="regular-text">
          <p class="description">Leave blank to reuse the RazMania Cards key.</p></td></tr>
        <tr><th>Canonical index URL</th><td>
          <input type="url" name="rzi_page_url" value="<?php echo esc_attr(get_option('rzi_page_url', '')); ?>" class="regular-text" placeholder="https://razmania.com/index/">
          <p class="description">Printed in the citation block. Set it once and never change it — citations are only useful if they keep resolving.</p></td></tr>
      </table><?php submit_button(); ?>
      <h2>Shortcodes</h2>
      <pre>[razmania_index_page]                       the whole thing: masthead, both tiers, tape, charts, methodology, citation
[razmania_index]                            broad index ($2,000+), chart + category cards
[razmania_index tier="bluechip"]            blue-chip index ($10,000+)
[razmania_index vertical="Pokemon"]         one category
[razmania_index_hero]                       just the two headline numbers — for the homepage
[razmania_ticker]                           the live tape — for the homepage
[razmania_index_methodology]                the academic section on its own</pre>
      <p class="description"><strong>Two tiers, two lags.</strong> Blue Chip is $10,000+ and settles in 2 days
      because that slice is scraped daily. The broad index is $2,000+ and settles in 4 days because the
      $2,000&ndash;9,999 tail is scraped every 3 days. Both lags come from the scraper config. Never "fix" a
      lag with <code>include_unsettled</code> &mdash; that publishes the sawtooth the design exists to stop.</p>
      <p><a href="<?php echo esc_url(admin_url('options-general.php?page=rzi&rzi_flush=1')); ?>">Clear cached API responses</a></p>
    </form></div>
<?php }

/* ------------------------------------------------------------------- fetch */
function rzi_get($path) {
    $base = rzi_api_base();
    if (!$base) return new WP_Error('rzi', 'API base URL not set');
    $key = 'rzi_' . md5($base . $path);
    $hit = get_transient($key);
    if ($hit !== false) return $hit;

    $args = ['timeout' => 8, 'headers' => ['Accept' => 'application/json']];
    if ($k = rzi_api_key()) $args['headers']['x-api-key'] = $k;
    $res = wp_remote_get($base . $path, $args);
    $ok  = !is_wp_error($res) && wp_remote_retrieve_response_code($res) === 200;
    if (!$ok) {
        // Serve the last good copy rather than an error. A stale index beats a
        // broken page, and it is labelled with its own settled date anyway.
        $stale = get_transient($key . '_stale');
        if ($stale !== false) return $stale;
        return is_wp_error($res) ? $res
             : new WP_Error('rzi', 'API returned ' . wp_remote_retrieve_response_code($res));
    }
    $data = json_decode(wp_remote_retrieve_body($res), true);
    set_transient($key, $data, RZI_CACHE_TTL);
    set_transient($key . '_stale', $data, WEEK_IN_SECONDS);
    return $data;
}

/* ----------------------------------------------------------------- helpers */
function rzi_money($n, $dec = 0) {
    if ($n === null || $n === '') return '—';
    return '$' . number_format((float)$n, $dec);
}
function rzi_date($iso) {
    if (!$iso) return '—';
    $t = strtotime($iso);
    return $t ? date_i18n('j M Y', $t) : $iso;
}
function rzi_pct($v, $suffix = '') {
    if ($v === null || $v === '') return '<span class="rzi-flat">—</span>';
    $v = (float)$v;
    $cls = $v > 0.05 ? 'up' : ($v < -0.05 ? 'down' : 'flat');
    $arrow = $v > 0.05 ? '▲' : ($v < -0.05 ? '▼' : '▬');
    return '<span class="rzi-' . $cls . '">' . $arrow . ' ' . ($v > 0 ? '+' : '')
         . number_format($v, 1) . '%' . esc_html($suffix) . '</span>';
}
function rzi_err($e) {
    return '<p class="rzi-error">The index is temporarily unavailable.<!-- '
         . esc_html(is_wp_error($e) ? $e->get_error_message() : 'no data') . ' --></p>';
}
function rzi_by_vertical($series) {
    $by = [];
    foreach ((array)$series as $r) $by[$r['vertical']][] = $r;
    return $by;
}
function rzi_head($d, $vertical = 'All') {
    foreach ((array)($d['latest'] ?? []) as $r) if ($r['vertical'] === $vertical) return $r;
    return null;
}

/* ------------------------------------------------------------------ charts */
/**
 * Inline SVG. Server-side, not a JS chart library, for the same reason the
 * numbers are: a crawler must see the index without executing anything.
 * The y-axis ALWAYS contains 100 — an index chart that crops its own baseline
 * turns a 2% drift into a visual collapse.
 */
function rzi_chart($pts, $label, $w = 800, $h = 280) {
    $n = count($pts);
    if ($n < 2) return '<p class="rzi-muted">Not enough settled history for a chart yet.</p>';
    $padL = 46; $padR = 18; $padT = 20; $padB = 30;
    $vals = array_map(function ($p) { return (float)$p['index_value']; }, $pts);
    $min = min(min($vals), 100.0); $max = max(max($vals), 100.0);
    if ($max - $min < 1.0) { $min -= 1.0; $max += 1.0; }
    $head = ($max - $min) * 0.12; $min -= $head; $max += $head; $span = $max - $min;
    $iw = $w - $padL - $padR; $ih = $h - $padT - $padB;
    $px = function ($i) use ($padL, $iw, $n) { return round($padL + $iw * $i / ($n - 1), 1); };
    $py = function ($v) use ($padT, $ih, $min, $span) { return round($padT + $ih * (1 - ($v - $min) / $span), 1); };
    $line = '';
    foreach ($vals as $i => $v) $line .= ($i ? ' L' : 'M') . $px($i) . ' ' . $py($v);
    $area = $line . ' L' . $px($n - 1) . ' ' . ($padT + $ih) . ' L' . $px(0) . ' ' . ($padT + $ih) . ' Z';
    $first = $pts[0]; $last = $pts[$n - 1];
    $up = (float)$last['index_value'] >= (float)$first['index_value'];
    $stroke = $up ? 'var(--up,#0a7d33)' : 'var(--down,#c0392b)';
    $uid = 'rzi' . substr(md5($label . $n . $last['as_of']), 0, 6);
    ob_start(); ?>
    <svg class="rzi-chart" viewBox="0 0 <?php echo $w; ?> <?php echo $h; ?>" role="img" aria-labelledby="<?php echo $uid; ?>">
      <title id="<?php echo $uid; ?>"><?php printf('%s, %s to %s: %s to %s, base 100',
        esc_html($label), esc_html($first['as_of']), esc_html($last['as_of']),
        number_format((float)$first['index_value'], 1), number_format((float)$last['index_value'], 1)); ?></title>
      <line x1="<?php echo $padL; ?>" x2="<?php echo $w - $padR; ?>" y1="<?php echo $py(100.0); ?>" y2="<?php echo $py(100.0); ?>" class="rzi-base"/>
      <text x="<?php echo $padL - 8; ?>" y="<?php echo $py(100.0) + 4; ?>" text-anchor="end" class="rzi-axis">100</text>
      <text x="<?php echo $padL - 8; ?>" y="<?php echo $padT + 4; ?>" text-anchor="end" class="rzi-axis"><?php echo number_format($max, 0); ?></text>
      <text x="<?php echo $padL - 8; ?>" y="<?php echo $padT + $ih; ?>" text-anchor="end" class="rzi-axis"><?php echo number_format($min, 0); ?></text>
      <path d="<?php echo $area; ?>" fill="<?php echo $stroke; ?>" opacity=".08"/>
      <path d="<?php echo $line; ?>" fill="none" stroke="<?php echo $stroke; ?>" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="<?php echo $px($n - 1); ?>" cy="<?php echo $py((float)$last['index_value']); ?>" r="4" fill="<?php echo $stroke; ?>"/>
      <text x="<?php echo $padL; ?>" y="<?php echo $h - 9; ?>" class="rzi-axis"><?php echo esc_html(rzi_date($first['as_of'])); ?></text>
      <text x="<?php echo $w - $padR; ?>" y="<?php echo $h - 9; ?>" text-anchor="end" class="rzi-axis"><?php echo esc_html(rzi_date($last['as_of'])); ?></text>
    </svg>
    <?php return ob_get_clean();
}
function rzi_spark($pts, $w = 160, $h = 36) {
    $n = count($pts);
    if ($n < 2) return '';
    $vals = array_map(function ($p) { return (float)$p['index_value']; }, $pts);
    $min = min($vals); $max = max($vals);
    if ($max - $min < 0.01) { $min -= 1; $max += 1; }
    $d = '';
    foreach ($vals as $i => $v)
        $d .= ($i ? ' L' : 'M') . round($w * $i / ($n - 1), 1) . ' ' . round($h - 3 - ($h - 6) * ($v - $min) / ($max - $min), 1);
    $up = end($vals) >= reset($vals);
    return '<svg class="rzi-spark" viewBox="0 0 ' . $w . ' ' . $h . '" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
         . '<path d="' . $d . '" fill="none" stroke="' . ($up ? 'var(--up,#0a7d33)' : 'var(--down,#c0392b)')
         . '" stroke-width="1.7" stroke-linejoin="round"/></svg>';
}

/* ----------------------------------------------------------------- blocks */
/** One tier's headline tile. */
function rzi_tile($d, $name, $sub) {
    $h = rzi_head($d);
    if (!$h) return '<div class="rzi-tile rzi-tile--empty"><span class="rzi-eyebrow">' . esc_html($name)
                  . '</span><p class="rzi-muted">Building its base period — needs about two weeks of settled data.</p></div>';
    ob_start(); ?>
    <div class="rzi-tile">
      <span class="rzi-eyebrow"><?php echo esc_html($name); ?></span>
      <span class="rzi-tile-val"><?php echo number_format((float)$h['index_value'], 2); ?></span>
      <span class="rzi-tile-deltas"><?php echo rzi_pct($h['pct_change_7d'], ' 7d'); ?> <?php echo rzi_pct($h['pct_change_30d'], ' 30d'); ?></span>
      <span class="rzi-tile-sub"><?php echo esc_html($sub); ?> · settled <?php echo esc_html(rzi_date($d['settled_through'])); ?></span>
    </div>
    <?php return ob_get_clean();
}

/** Category grid for one tier. */
function rzi_cards($d) {
    $by = rzi_by_vertical($d['series'] ?? []);
    $out = '<div class="rzi-grid">';
    $n = 0;
    foreach ((array)($d['latest'] ?? []) as $r) {
        if ($r['vertical'] === 'All') continue;
        $n++;
        $out .= '<div class="rzi-card"><div class="rzi-card-top"><span class="rzi-cat">' . esc_html($r['vertical'])
              . '</span><span class="rzi-num">' . number_format((float)$r['index_value'], 1) . '</span></div>'
              . rzi_spark($by[$r['vertical']] ?? [])
              . '<div class="rzi-card-bot">' . rzi_pct($r['pct_change_7d'], ' 7d')
              . '<span class="rzi-muted">median ' . rzi_money($r['median_price']) . '</span></div></div>';
    }
    $out .= '</div>';
    if (!$n) return '';
    return $out;
}

function rzi_dataset_ld($d, $name) {
    return [
        '@context' => 'https://schema.org', '@type' => 'Dataset',
        'name' => $name,
        'description' => 'Trailing 7-day median price of confirmed eBay trading-card sales over $'
            . number_format($d['floor']) . ', rebased to 100 at ' . $d['base_date']
            . '. Best-offer-accepted listings are excluded because eBay publishes the seller asking '
            . 'price on those, not the amount paid. Settles ' . (int)$d['settle_days'] . ' days behind the newest tracked sale.',
        'temporalCoverage' => $d['base_date'] . '/' . $d['settled_through'],
        'dateModified' => $d['settled_through'],
        'variableMeasured' => 'Index value, base 100',
        'creator' => ['@type' => 'Organization', 'name' => 'RazMania', 'url' => 'https://razmania.com'],
        'license' => 'https://creativecommons.org/licenses/by/4.0/',
        'isAccessibleForFree' => true,
        'version' => RZI_METHOD_VER,
    ];
}

/* -------------------------------------------------------------- shortcodes */
add_shortcode('razmania_index', function ($a) {
    $a = shortcode_atts(['tier' => 'all', 'vertical' => '', 'days' => 180, 'title' => '',
                         'chart' => 'yes', 'cards' => 'yes'], $a);
    $tier = $a['tier'] === 'bluechip' ? 'bluechip' : 'all';
    if (!$a['title']) $a['title'] = $tier === 'bluechip' ? 'The RazMania Blue Chip Index' : 'The RazMania Index';
    $d = rzi_get('/v1/index?tier=' . $tier . '&days=' . (int)$a['days']);
    if (is_wp_error($d)) return rzi_err($d);
    if (empty($d['series'])) return '<p class="rzi-muted">The index is still building its base period.</p>';

    $focus = $a['vertical'] ?: 'All';
    $head = rzi_head($d, $focus);
    if (!$head) return '<p class="rzi-muted">No index for that category yet.</p>';
    $by = rzi_by_vertical($d['series']);
    $label = $focus === 'All' ? 'All tracked cards' : $focus;
    $lag = (int)$d['settle_days'];
    ob_start(); ?>
    <script type="application/ld+json"><?php echo wp_json_encode(rzi_dataset_ld($d, $a['title'] . ' — ' . $label)); ?></script>
    <section class="rzi rzi-index">
      <div class="rzi-head">
        <div><h2 class="rzi-h2"><?php echo esc_html($a['title']); ?></h2>
          <p class="rzi-sub"><?php echo esc_html($label); ?> · over $<?php echo number_format($d['floor']); ?> · base 100 at <?php echo esc_html(rzi_date($d['base_date'])); ?></p></div>
        <div class="rzi-now"><span class="rzi-val"><?php echo number_format((float)$head['index_value'], 2); ?></span>
          <span class="rzi-deltas"><?php echo rzi_pct($head['pct_change_7d'], ' 7d'); ?> <?php echo rzi_pct($head['pct_change_30d'], ' 30d'); ?></span></div>
      </div>
      <?php if ($a['chart'] === 'yes' && !empty($by[$focus])) echo rzi_chart($by[$focus], $label); ?>
      <?php if ($a['cards'] === 'yes') echo rzi_cards($d); ?>
      <p class="rzi-asof">Tracked sales over <strong>$<?php echo number_format($d['floor']); ?></strong>. Trailing 7-day median,
        rebased to 100 at <?php echo esc_html(rzi_date($d['base_date'])); ?>. Best-offer-accepted listings are excluded, because
        eBay publishes the seller's asking price on those rather than the amount paid. Settled through
        <strong><?php echo esc_html(rzi_date($d['settled_through'])); ?></strong> — <?php echo $lag; ?> day<?php echo $lag === 1 ? '' : 's'; ?> behind
        the newest tracked sale, which is how long every sale in this range takes to collect.</p>
    </section>
    <?php return ob_get_clean();
});

/** Homepage-sized: the two headline numbers and one line of claim. */
add_shortcode('razmania_index_hero', function ($a) {
    $a = shortcode_atts(['link' => ''], $a);
    $bc = rzi_get('/v1/index?tier=bluechip&days=45');
    $al = rzi_get('/v1/index?tier=all&days=45');
    if (is_wp_error($bc) && is_wp_error($al)) return rzi_err($bc);
    $link = $a['link'] ?: get_option('rzi_page_url', '');
    ob_start(); ?>
    <section class="rzi rzi-hero">
      <div class="rzi-hero-text">
        <span class="rzi-eyebrow">The RazMania Index</span>
        <h2 class="rzi-hero-h">A daily measure of the card market, built only from prices somebody actually paid.</h2>
        <?php if ($link): ?><a class="rzi-cta" href="<?php echo esc_url($link); ?>">Read the index and how it's built →</a><?php endif; ?>
      </div>
      <div class="rzi-tiles">
        <?php echo is_wp_error($bc) ? '' : rzi_tile($bc, 'Blue Chip · $10,000+', 'Scraped daily'); ?>
        <?php echo is_wp_error($al) ? '' : rzi_tile($al, 'Broad · $2,000+', 'Full market read'); ?>
      </div>
    </section>
    <?php return ob_get_clean();
});

/** The live tape. Reads the leaderboard — current, because only $10k+ sales reach it and that tier is daily. */
add_shortcode('razmania_ticker', function ($a) {
    $a = shortcode_atts(['limit' => 20, 'vertical' => '', 'speed' => 70], $a);
    $path = '/v1/leaderboard?limit=' . (int)$a['limit'];
    if ($a['vertical']) $path .= '&vertical=' . rawurlencode($a['vertical']);
    $d = rzi_get($path);
    if (is_wp_error($d) || empty($d['results'])) return '';
    $items = '';
    foreach ($d['results'] as $r) {
        $items .= '<a class="rzi-tape-item" href="' . esc_url($r['url']) . '" rel="nofollow noopener" target="_blank">'
                . '<strong>' . esc_html(rzi_money($r['total_price'])) . '</strong>'
                . '<span>' . esc_html(wp_trim_words($r['title'], 11, '…')) . '</span>'
                . '<em>' . esc_html($r['vertical']) . '</em></a>';
    }
    ob_start(); ?>
    <div class="rzi rzi-tape" role="region" aria-label="Biggest confirmed card sales, last 7 days">
      <span class="rzi-tape-tag">Confirmed · last 7 days</span>
      <div class="rzi-tape-win"><div class="rzi-tape-track" style="--rzi-speed:<?php echo (int)$a['speed']; ?>s">
        <?php echo $items; ?><span aria-hidden="true"><?php echo $items; ?></span>
      </div></div>
    </div>
    <?php return ob_get_clean();
});

/**
 * The methodology. Written as a paper, with the live numbers filled in from
 * the API so it can never describe an index other than the one on the page.
 */
add_shortcode('razmania_index_methodology', function () {
    $al = rzi_get('/v1/index?tier=all&days=30');
    $bc = rzi_get('/v1/index?tier=bluechip&days=30');
    $st = rzi_get('/v1/stats');
    if (is_wp_error($al)) return rzi_err($al);
    $floor   = number_format($al['floor']);
    $hot     = is_wp_error($bc) ? '10,000' : number_format($bc['floor']);
    $lagA    = (int)$al['settle_days'];
    $lagB    = is_wp_error($bc) ? 2 : (int)$bc['settle_days'];
    $share   = (!is_wp_error($st) && isset($st['best_offer_share']) && $st['best_offer_share'] !== null)
             ? number_format((float)$st['best_offer_share'] * 100, 0) . '%' : 'roughly three in ten';
    $since   = !is_wp_error($st) && !empty($st['first_date']) ? rzi_date($st['first_date']) : 'July 2026';
    $tracked = !is_wp_error($st) && !empty($st['confirmed_sales']) ? number_format($st['confirmed_sales']) : null;
    $url     = get_option('rzi_page_url', '') ?: home_url('/index/');
    $today   = date_i18n('j M Y');
    ob_start(); ?>
    <section class="rzi rzi-method" id="methodology">
      <span class="rzi-eyebrow">Methodology · v<?php echo RZI_METHOD_VER; ?></span>
      <h2 class="rzi-h2">How the RazMania Index is built</h2>
      <p class="rzi-abstract"><strong>Abstract.</strong> The RazMania Index is a daily, fixed-base price index of the
        trading-card market, constructed from confirmed eBay sales. It differs from every free "sold price" tracker in one
        respect that turns out to matter more than any other: listings closed by <em>Best Offer</em> — <?php echo $share; ?> of
        listings over $<?php echo $floor; ?> — are excluded, because for those eBay publishes the seller's asking price, not
        the amount the buyer paid. The index is published in two tiers with different settlement lags that follow directly
        from how often each price range is collected, and every point is published exactly once, after it has settled.
        Nothing on this page is revised.</p>

      <ol class="rzi-sections">
        <li><h3>Data</h3>
          <p>Completed eBay listings in the <em>Sports Trading Card Singles</em> and <em>CCG Singles</em> categories,
          collected daily since <?php echo esc_html($since); ?><?php if ($tracked) echo ', ' . $tracked . ' confirmed sales to date'; ?>.
          Only sales at or above <strong>$<?php echo $floor; ?></strong> are collected, and that floor is enforced a second time inside the
          database, so no published aggregate can ever include a price range that was only partially observed.
          Every figure on this site is therefore a statement about <em>tracked sales over $<?php echo $floor; ?></em>, never about
          "all card sales", and is labelled as such.</p></li>

        <li><h3>Exclusions</h3>
          <p>Three classes of listing are removed before any calculation. <strong>Best-offer-accepted listings</strong>, for the
          reason above — their published price is a ceiling, not a transaction. <strong>Lots, reprints, customs and
          "you pick" listings</strong>, which are not single-card sales. And the <strong>unclassified residue</strong> — listings whose
          title names no sport, game, player or set — which is a property of the classifier rather than of the market,
          and is excluded from the index outright rather than allowed to move it.</p></li>

        <li><h3>Construction</h3>
          <p>For each category and each day <em>t</em>, the index observes every confirmed sale in the trailing seven-day window
          (<em>t</em>−6 … <em>t</em>) and takes the <strong>median</strong> price. The median, not the mean: a single six-figure sale
          would otherwise dominate a week. That median is divided by the category's <strong>base median</strong> and multiplied
          by 100, so every series opens at exactly 100.00 and a reading of 113 means the middle of that market is 13%
          dearer than it was at the base date.</p></li>

        <li><h3>The composite</h3>
          <p>The headline number blends the categories at <strong>fixed weights</strong> equal to each category's share of dollar
          volume in the base period. Weights never change. This is what makes it an index rather than a pooled median:
          a pooled median moves when the <em>mix</em> of what sells moves — a loud week in Pokémon and a quiet one in basketball
          shifts it without a single card changing hands at a different price. Under a pure volume shock the composite
          moves by 0.00; a pooled median over the same data moves by roughly nine percent. The set of constituent
          categories is frozen at launch, so a category qualifying later cannot step the composite by joining at 100
          while the others sit elsewhere.</p></li>

        <li><h3>Two tiers, and why their lags differ</h3>
          <p>Sales of <strong>$<?php echo $hot; ?> and above</strong> are collected every day. Sales between $<?php echo $floor; ?> and
          $<?php echo $hot; ?> are collected every three days. A day cannot be published until every price range has swept it,
          so the collection cadence <em>is</em> the publication lag:</p>
          <table class="rzi-table"><thead><tr><th>Tier</th><th>Range</th><th>Collected</th><th>Settles in</th></tr></thead>
            <tbody><tr><td>Blue Chip</td><td>$<?php echo $hot; ?>+</td><td>daily</td><td><?php echo $lagB; ?> days</td></tr>
                   <tr><td>Broad</td><td>$<?php echo $floor; ?>+</td><td>every 3 days</td><td><?php echo $lagA; ?> days</td></tr></tbody></table>
          <p>Until the slower range lands, the most recent days contain only the most expensive sales and read
          materially high — on data calibrated to this market's real price split, roughly nine percent high at the current
          cadence, and more than double under a weekly one. Publishing those days would show the hobby surging and
          collapsing on a cycle set entirely by the collection schedule. So they are not published. The lag is the
          honest cost of the data, not a defect in it.</p></li>

        <li><h3>Minimum sample</h3>
          <p>A category publishes a point only when its seven-day window holds at least <strong>20 confirmed sales</strong>. Card
          prices are approximately log-normal with σ ≈ 0.78, which puts the standard error of a weekly median near
          ±44% at <em>n</em> = 5 and ±22% at <em>n</em> = 20. Below the threshold a category shows a gap rather than a line. The Blue
          Chip tier, being roughly nine percent of volume, therefore carries fewer categories than the broad index. That
          is the correct outcome of the rule, not a limitation to be tuned away.</p></li>

        <li><h3>Base period and revision policy</h3>
          <p>Each category's base date, base median and composite weight were fixed once, at the first seven-day window in
          which it qualified, and are stored independently of the underlying sales. Older sales are pruned after 400
          days; the base is not recomputed when they are, so the historical series does not move. <strong>Published points
          are never revised.</strong> A change to the collection floor would invalidate the base and be handled as a
          deliberate, announced relaunch of the series with a new base date and a new methodology version — never as a
          silent re-pin.</p></li>

        <li><h3>Limitations</h3>
          <ul>
            <li>It measures the <strong>$<?php echo $floor; ?>+ market on eBay</strong>. It says nothing about cards below that floor, about
              auction houses, or about private sales.</li>
            <li>Categories are assigned from listing titles by a keyword classifier. A mis-titled listing is a
              mis-categorised sale.</li>
            <li>The median is a level of the market's middle, not the value of any particular card. It will not tell you
              what your card is worth; it tells you which way the market it sits in is moving.</li>
            <li>The series is young. Treat readings in the first months as a level, not yet as a trend.</li>
          </ul></li>

        <li><h3>How to cite</h3>
          <p>Cite the tier, the value, the settled date and the base date. All four are on the page and all four are
          stable.</p>
          <blockquote class="rzi-cite">RazMania (<?php echo date_i18n('Y'); ?>). <em>The RazMania Index</em>, methodology v<?php echo RZI_METHOD_VER; ?>.
            <?php echo esc_html($url); ?>. Retrieved <?php echo esc_html($today); ?>.</blockquote>
          <p class="rzi-muted">Published under CC BY 4.0. Reproduce the number freely; keep the floor label and the settled
            date attached to it.</p></li>
      </ol>
    </section>
    <?php return ob_get_clean();
});

/** The whole landing page in one shortcode. Drop it on an empty full-width page. */
add_shortcode('razmania_index_page', function () {
    $bc = rzi_get('/v1/index?tier=bluechip&days=365');
    $al = rzi_get('/v1/index?tier=all&days=365');
    $st = rzi_get('/v1/stats');
    if (is_wp_error($al) && is_wp_error($bc)) return rzi_err($al);
    $share = (!is_wp_error($st) && isset($st['best_offer_share']) && $st['best_offer_share'] !== null)
           ? number_format((float)$st['best_offer_share'] * 100, 0) . '%' : 'about 30%';
    $floor = is_wp_error($al) ? '2,000' : number_format($al['floor']);
    ob_start(); ?>
    <article class="rzi rzi-page">
      <?php if (!is_wp_error($bc) && !empty($bc['series'])): ?>
      <script type="application/ld+json"><?php echo wp_json_encode(rzi_dataset_ld($bc, 'The RazMania Blue Chip Index')); ?></script>
      <?php endif; if (!is_wp_error($al) && !empty($al['series'])): ?>
      <script type="application/ld+json"><?php echo wp_json_encode(rzi_dataset_ld($al, 'The RazMania Index')); ?></script>
      <?php endif; ?>

      <header class="rzi-mast">
        <span class="rzi-eyebrow">A daily measure of the trading-card market</span>
        <h1 class="rzi-h1">The RazMania Index</h1>
        <p class="rzi-dek">Built only from prices somebody actually paid. <?php echo esc_html($share); ?> of eBay listings over
          $<?php echo $floor; ?> close by Best Offer, and on those eBay shows the seller's asking price — not the sale.
          Every other free tracker counts them. This one can't.</p>
        <div class="rzi-tiles rzi-tiles--mast">
          <?php echo is_wp_error($bc) ? '' : rzi_tile($bc, 'Blue Chip · $10,000+', 'Scraped daily'); ?>
          <?php echo is_wp_error($al) ? '' : rzi_tile($al, 'Broad · $2,000+', 'Full market read'); ?>
        </div>
      </header>

      <?php echo do_shortcode('[razmania_ticker limit="24"]'); ?>

      <?php if (!is_wp_error($bc) && !empty($bc['series'])):
        $by = rzi_by_vertical($bc['series']); $h = rzi_head($bc); ?>
      <section class="rzi-index">
        <div class="rzi-head"><div><h2 class="rzi-h2">Blue Chip</h2>
          <p class="rzi-sub">Confirmed sales over $<?php echo number_format($bc['floor']); ?> · base 100 at <?php echo esc_html(rzi_date($bc['base_date'])); ?> · settles in <?php echo (int)$bc['settle_days']; ?> days</p></div>
          <?php if ($h): ?><div class="rzi-now"><span class="rzi-val"><?php echo number_format((float)$h['index_value'], 2); ?></span>
          <span class="rzi-deltas"><?php echo rzi_pct($h['pct_change_7d'], ' 7d'); ?> <?php echo rzi_pct($h['pct_change_30d'], ' 30d'); ?></span></div><?php endif; ?></div>
        <?php if (!empty($by['All'])) echo rzi_chart($by['All'], 'Blue Chip composite'); echo rzi_cards($bc); ?>
      </section>
      <?php endif; ?>

      <?php if (!is_wp_error($al) && !empty($al['series'])):
        $by = rzi_by_vertical($al['series']); $h = rzi_head($al); ?>
      <section class="rzi-index">
        <div class="rzi-head"><div><h2 class="rzi-h2">Broad market</h2>
          <p class="rzi-sub">Confirmed sales over $<?php echo number_format($al['floor']); ?> · base 100 at <?php echo esc_html(rzi_date($al['base_date'])); ?> · settles in <?php echo (int)$al['settle_days']; ?> days</p></div>
          <?php if ($h): ?><div class="rzi-now"><span class="rzi-val"><?php echo number_format((float)$h['index_value'], 2); ?></span>
          <span class="rzi-deltas"><?php echo rzi_pct($h['pct_change_7d'], ' 7d'); ?> <?php echo rzi_pct($h['pct_change_30d'], ' 30d'); ?></span></div><?php endif; ?></div>
        <?php if (!empty($by['All'])) echo rzi_chart($by['All'], 'Broad composite'); echo rzi_cards($al); ?>
      </section>
      <?php elseif (!is_wp_error($al)): ?>
      <section class="rzi-index"><h2 class="rzi-h2">Broad market</h2>
        <p class="rzi-muted">Building its base period. The broad index needs roughly two weeks of settled data before it publishes a first point; it will appear here on its own.</p></section>
      <?php endif; ?>

      <?php echo do_shortcode('[razmania_index_methodology]'); ?>
    </article>
    <?php return ob_get_clean();
});

/* ------------------------------------------------------------------ styles */
add_action('wp_enqueue_scripts', function () {
    wp_register_style('rzi', false, [], RZI_VERSION);
    wp_enqueue_style('rzi');
    wp_add_inline_style('rzi', '
    .rzi{--rzi-ink:var(--ink,#14110D);--rzi-ink2:var(--ink2,#57514A);--rzi-ink3:var(--ink3,#6E6862);--rzi-bg:var(--bg,#FBF9F5);--rzi-s1:var(--s1,#fff);--rzi-s2:var(--s2,#F2EDE4);--rzi-line:var(--line,rgba(26,22,16,.11));--rzi-line2:var(--line2,rgba(26,22,16,.2));--rzi-gold:var(--gold,#9A6B00);
         color:var(--rzi-ink);font-variant-numeric:tabular-nums;line-height:1.5}
    .rzi *{box-sizing:border-box}
    .rzi-up{color:var(--up,#0a7d33)}.rzi-down{color:var(--down,#b3261e)}.rzi-flat,.rzi-muted{color:var(--rzi-ink3)}
    .rzi-muted{font-size:13px}
    .rzi-eyebrow{display:block;font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--rzi-gold)}
    .rzi-h1{font-family:Georgia,"Times New Roman",serif;font-weight:700;font-size:clamp(40px,6vw,68px);line-height:1;letter-spacing:-.02em;margin:10px 0 18px}
    .rzi-h2{font-family:Georgia,"Times New Roman",serif;font-size:clamp(24px,3vw,32px);line-height:1.15;margin:0}
    .rzi-dek{font-size:clamp(17px,2vw,21px);line-height:1.45;color:var(--rzi-ink2);max-width:62ch;margin:0 0 28px}
    /* masthead + tiles */
    .rzi-mast{padding:34px 0 26px;border-bottom:1px solid var(--rzi-line2)}
    .rzi-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
    .rzi-tile{background:var(--rzi-s1);border:1px solid var(--rzi-line);border-radius:12px;padding:18px 20px;display:flex;flex-direction:column;gap:4px}
    .rzi-tile-val{font-size:clamp(42px,5vw,56px);font-weight:800;line-height:1;letter-spacing:-.02em}
    .rzi-tile-deltas{font-size:14px}.rzi-tile-deltas>span+span{margin-left:12px}
    .rzi-tile-sub{font-size:12px;color:var(--rzi-ink3)}
    /* hero (homepage) */
    .rzi-hero{display:grid;grid-template-columns:1.1fr 1fr;gap:28px;align-items:center;padding:28px 0;border-top:1px solid var(--rzi-line2);border-bottom:1px solid var(--rzi-line2)}
    .rzi-hero-h{font-family:Georgia,serif;font-size:clamp(24px,3.2vw,34px);line-height:1.15;margin:8px 0 14px}
    .rzi-cta{font-weight:700;color:var(--rzi-ink);text-decoration:none;border-bottom:2px solid var(--rzi-gold)}
    @media(max-width:760px){.rzi-hero{grid-template-columns:1fr}}
    /* index section */
    .rzi-index{padding:30px 0 10px}
    .rzi-head{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end;justify-content:space-between;border-bottom:2px solid var(--rzi-line2);padding-bottom:12px}
    .rzi-sub{margin:4px 0 0;font-size:13px;color:var(--rzi-ink3)}
    .rzi-now{text-align:right;line-height:1.05}
    .rzi-val{display:block;font-size:44px;font-weight:800;letter-spacing:-.02em}
    .rzi-deltas{display:block;margin-top:4px;font-size:13px}.rzi-deltas>span+span{margin-left:10px}
    .rzi-chart{display:block;width:100%;height:auto;margin:18px 0 6px}
    .rzi-axis{font-size:11px;fill:var(--rzi-ink3);font-family:inherit}
    .rzi-base{stroke:var(--rzi-line2);stroke-width:1;stroke-dasharray:3 3}
    .rzi-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:10px;margin:16px 0}
    .rzi-card{background:var(--rzi-s1);border:1px solid var(--rzi-line);border-radius:10px;padding:11px 13px}
    .rzi-card-top{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
    .rzi-cat{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--rzi-ink3)}
    .rzi-num{font-size:21px;font-weight:700}
    .rzi-spark{display:block;width:100%;height:36px;margin:6px 0}
    .rzi-card-bot{display:flex;justify-content:space-between;gap:8px;font-size:12px}
    .rzi-asof{font-size:13px;color:var(--rzi-ink3);line-height:1.5;margin:12px 0 0}
    .rzi-error{font-size:14px;color:var(--rzi-ink3)}
    /* tape */
    .rzi-tape{display:flex;border:1px solid var(--rzi-line2);border-radius:10px;overflow:hidden;background:var(--rzi-ink);margin:22px 0}
    .rzi-tape-tag{flex:0 0 auto;display:flex;align-items:center;padding:0 14px;background:var(--rzi-gold);color:#fff;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;white-space:nowrap}
    .rzi-tape-win{flex:1 1 auto;overflow:hidden}
    .rzi-tape-track{display:inline-flex;white-space:nowrap;will-change:transform;animation:rzi-tape var(--rzi-speed,70s) linear infinite}
    .rzi-tape-track>span{display:inline-flex}
    .rzi-tape:hover .rzi-tape-track{animation-play-state:paused}
    .rzi-tape-item{display:inline-flex;align-items:baseline;gap:9px;padding:12px 18px;color:#f1ede6;text-decoration:none;font-size:13px;border-right:1px solid rgba(255,255,255,.12)}
    .rzi-tape-item strong{color:#F5C518}
    .rzi-tape-item em{color:#a8a199;font-style:normal;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
    .rzi-tape-item:hover{background:rgba(255,255,255,.06)}
    @keyframes rzi-tape{from{transform:translateX(0)}to{transform:translateX(-50%)}}
    @media(prefers-reduced-motion:reduce){.rzi-tape-track{animation:none}.rzi-tape-win{overflow-x:auto}.rzi-tape-track>span{display:none}}
    /* methodology */
    .rzi-method{padding:36px 0 20px;border-top:1px solid var(--rzi-line2);margin-top:24px;max-width:78ch}
    .rzi-abstract{font-size:17px;line-height:1.6;margin:14px 0 26px;padding:16px 20px;background:var(--rzi-s2);border-left:3px solid var(--rzi-gold);border-radius:0 8px 8px 0}
    .rzi-sections{counter-reset:rzi;list-style:none;padding:0;margin:0}
    .rzi-sections>li{counter-increment:rzi;padding:16px 0 12px;border-top:1px solid var(--rzi-line)}
    .rzi-sections h3{font-family:Georgia,serif;font-size:20px;margin:0 0 8px}
    .rzi-sections h3::before{content:counter(rzi) ".";color:var(--rzi-gold);margin-right:10px}
    .rzi-sections p,.rzi-sections li{font-size:15.5px;line-height:1.65}
    .rzi-sections ul{padding-left:20px}
    .rzi-table{width:100%;border-collapse:collapse;font-size:14px;margin:10px 0 14px}
    .rzi-table th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--rzi-ink3);border-bottom:2px solid var(--rzi-line2);padding:6px 8px}
    .rzi-table td{padding:8px;border-bottom:1px solid var(--rzi-line)}
    .rzi-cite{margin:10px 0;padding:14px 18px;background:var(--rzi-s1);border:1px solid var(--rzi-line);border-radius:8px;font-family:Georgia,serif;font-size:15px}
    ');
});
