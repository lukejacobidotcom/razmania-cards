<?php
/**
 * Plugin Name: RazMania Card Movers
 * Description: Card Movers — the graded cards whose confirmed eBay sale prices moved most, the busiest cards, and a price chart for any card a reader searches. The page and every card chart are rendered by the card-data API and hosted here server-side, so prices and sales are in the raw HTML.
 * Version:     1.0.0
 * Author:      RazMania
 *
 * A separate plugin, like razmania-index and razmania-firsts, so nothing here
 * can overwrite the live razmania-cards plugin. Namespaced rzmv_ / .rzmv / .mv.
 *
 * What a card is and what counts as a move is decided in the database
 * (db/schema.sql, "CARD MOVERS"); the API words it (api/movers.py). This file
 * only fetches, caches and hosts, and never edits a number.
 *
 * ROUTES
 *   /movers/                     a normal WordPress page carrying [razmania_movers]
 *                                (?vertical=Pokemon filters, ?q=gengar+108 searches)
 *   /movers/c/<id>-<slug>/       one card: every confirmed sale on a chart (API fragment)
 *   /movers/c/<id>/              same, 301 to the slugged URL
 *
 * The filter parameter is `vertical`, not `category`: `category`, `cat` and
 * `s` are WordPress query words and would turn the page into an archive.
 */

if (!defined('ABSPATH')) exit;

define('RZMV_VERSION',   '1.0.0');
define('RZMV_CACHE_TTL', 30 * MINUTE_IN_SECONDS);

/* ------------------------------------------------------------------ config */
function rzmv_api_base() {
    foreach (['rzmv_api_base', 'rzf_api_base', 'rzi_api_base', 'rzm_api_base'] as $k) {
        $v = get_option($k, '');
        if ($v) return rtrim($v, '/');
    }
    return '';
}
function rzmv_api_key() {
    foreach (['rzmv_api_key', 'rzf_api_key', 'rzi_api_key', 'rzm_api_key'] as $k) {
        $v = get_option($k, '');
        if ($v) return $v;
    }
    return '';
}
function rzmv_page_url() { return trailingslashit(get_option('rzmv_page_url', '') ?: home_url('/movers/')); }
function rzmv_page_path() { return trim((string)wp_parse_url(rzmv_page_url(), PHP_URL_PATH), '/'); }
function rzmv_card_url($id, $slug) { return rzmv_page_url() . 'c/' . $id . ($slug !== '' ? '-' . $slug : '') . '/'; }

add_action('admin_menu', function () {
    add_options_page('RazMania Card Movers', 'RazMania Card Movers', 'manage_options', 'rzmv', 'rzmv_settings_page');
});
add_action('admin_init', function () {
    foreach (['rzmv_api_base', 'rzmv_api_key', 'rzmv_page_url'] as $k) register_setting('rzmv', $k);
    if (!empty($_GET['rzmv_flush']) && current_user_can('manage_options')) {
        global $wpdb;
        $wpdb->query("DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient%rzmv_%'");
    }
});
// The card route lives beneath the page, so moving the page moves the rule.
add_action('update_option_rzmv_page_url', function () { rzmv_add_rules(); flush_rewrite_rules(false); });

function rzmv_settings_page() {
    $m = rzmv_get('/v1/movers?limit=3', 10 * MINUTE_IN_SECONDS); ?>
    <div class="wrap"><h1>RazMania Card Movers</h1>
    <p><strong>Now:</strong>
    <?php if (is_wp_error($m)): ?>API not reachable (<?php echo esc_html($m->get_error_message()); ?>).
    <?php else: ?>settled through <?php echo esc_html($m['window']['settled_through'] ?? '—'); ?>;
      top gainer <?php echo !empty($m['gainers']) ? esc_html($m['gainers'][0]['card_label'] . ' ' . $m['gainers'][0]['grade_label'] . ' +' . $m['gainers'][0]['change_pct'] . '%') : 'none'; ?>;
      top loser <?php echo !empty($m['losers']) ? esc_html($m['losers'][0]['card_label'] . ' ' . $m['losers'][0]['grade_label'] . ' ' . $m['losers'][0]['change_pct'] . '%') : 'none'; ?>.
    <?php endif; ?></p>
    <p>What makes a card, and what makes a move, is set in the database and tuned in <code>schema_meta</code>
       (<code>movers_*</code> keys). Nothing is editable here on purpose.</p>
    <form method="post" action="options.php"><?php settings_fields('rzmv'); ?>
      <table class="form-table">
        <tr><th>API base URL</th><td>
          <input type="url" name="rzmv_api_base" value="<?php echo esc_attr(get_option('rzmv_api_base', '')); ?>" class="regular-text">
          <p class="description">Leave blank to reuse the Firsts / Index / Cards setting (<?php echo esc_html(rzmv_api_base() ?: 'not set'); ?>).</p></td></tr>
        <tr><th>API key</th><td>
          <input type="text" name="rzmv_api_key" value="<?php echo esc_attr(get_option('rzmv_api_key', '')); ?>" class="regular-text">
          <p class="description">Leave blank to reuse the Firsts / Index / Cards key.</p></td></tr>
        <tr><th>Movers page URL</th><td>
          <input type="url" name="rzmv_page_url" value="<?php echo esc_attr(get_option('rzmv_page_url', '')); ?>" class="regular-text" placeholder="<?php echo esc_attr(home_url('/movers/')); ?>">
          <p class="description">The page carrying <code>[razmania_movers]</code>. Card charts live beneath it at <code>c/&lt;id&gt;-&lt;slug&gt;/</code>.
             The API prints its links from <code>MOVERS_SITE_URL</code> on the API service; keep the two the same.</p></td></tr>
      </table><?php submit_button(); ?>
      <h2>Shortcodes</h2>
      <pre>[razmania_movers]          the page: search any card, gainers, losers, busiest cards, method
[razmania_movers_strip]    three gainers and three losers, for the homepage or a sidebar</pre>
      <p><a href="<?php echo esc_url(admin_url('options-general.php?page=rzmv&rzmv_flush=1')); ?>">Clear cached API responses</a></p>
    </form></div>
<?php }

/* -------------------------------------------------------------------- data */
function rzmv_get($path, $ttl = RZMV_CACHE_TTL) {
    $base = rzmv_api_base();
    if (!$base) return new WP_Error('rzmv', 'API base URL not set');
    $key = 'rzmv_' . md5($base . $path);
    $hit = get_transient($key);
    if ($hit !== false) return $hit;
    $args = ['timeout' => 12, 'headers' => ['Accept' => 'application/json']];
    if ($k = rzmv_api_key()) $args['headers']['x-api-key'] = $k;
    $res = wp_remote_get($base . $path, $args);
    $code = is_wp_error($res) ? 0 : wp_remote_retrieve_response_code($res);
    if ($code === 404) return new WP_Error('rzmv_404', 'not found');
    if ($code !== 200) {
        // Yesterday's list is stale, not wrong: it carries its own "settled
        // through" date. Serve it rather than an error while the API is down.
        $stale = get_transient($key . '_stale');
        if ($stale !== false) return $stale;
        return is_wp_error($res) ? $res : new WP_Error('rzmv', 'API returned ' . $code);
    }
    $data = json_decode(wp_remote_retrieve_body($res), true);
    set_transient($key, $data, $ttl);
    set_transient($key . '_stale', $data, DAY_IN_SECONDS);
    return $data;
}

/** The API's stylesheet, once per request however many blocks are on the page. */
function rzmv_css($css) {
    static $done = false;
    if ($done || !$css) return '';
    $done = true;
    return '<style id="rzmv-css">' . $css . '</style>';
}

/** ?q= and ?vertical= from the request, validated the way the API validates them. */
function rzmv_request_args() {
    $q = isset($_GET['q']) ? trim(sanitize_text_field(wp_unslash($_GET['q']))) : '';
    $q = function_exists('mb_substr') ? mb_substr($q, 0, 80) : substr($q, 0, 80);
    $v = isset($_GET['vertical']) ? sanitize_text_field(wp_unslash($_GET['vertical'])) : '';
    if (!preg_match("/^[A-Za-z0-9 .'&-]{0,40}$/", $v)) $v = '';
    return [$q, $v];
}

/* -------------------------------------------------------------- shortcodes */
add_shortcode('razmania_movers', function () {
    list($q, $v) = rzmv_request_args();
    $args = ['fragment' => 1];
    if ($v !== '') $args['vertical'] = $v;
    $searching = strlen($q) >= 2;
    if ($searching) $args['q'] = $q;
    // Searches are free text: cache them briefly so they cannot pile up.
    $f = rzmv_get('/movers/?' . http_build_query($args), $searching ? 10 * MINUTE_IN_SECONDS : RZMV_CACHE_TTL);
    if (is_wp_error($f) || empty($f['html'])) {
        return '<p class="rzmv-muted">Card Movers is refreshing. Try again in a few minutes.</p>';
    }
    return rzmv_css($f['css']) . $f['html'];   // server-rendered by our own API; already escaped there
});

add_shortcode('razmania_movers_strip', function () {
    $f = rzmv_get('/movers/strip?fragment=1');
    if (is_wp_error($f) || empty($f['html'])) return '';
    return rzmv_css($f['css']) . $f['html'];
});

/* ----------------------------------------------------------------- routing */
function rzmv_add_rules() {
    $p = preg_quote(rzmv_page_path(), '#');
    add_rewrite_rule('^' . $p . '/c/([0-9a-f]{16})(?:-[a-z0-9\-]*)?/?$', 'index.php?rzmv=card&rzmv_id=$matches[1]', 'top');
}
add_action('init', 'rzmv_add_rules');
add_filter('query_vars', function ($v) { $v[] = 'rzmv'; $v[] = 'rzmv_id'; return $v; });
register_activation_hook(__FILE__, function () { rzmv_add_rules(); flush_rewrite_rules(); update_option('rzmv_rules_version', RZMV_VERSION); });
register_deactivation_hook(__FILE__, 'flush_rewrite_rules');
add_action('wp_loaded', function () {
    if (get_option('rzmv_rules_version') !== RZMV_VERSION) { flush_rewrite_rules(false); update_option('rzmv_rules_version', RZMV_VERSION); }
});

add_action('template_redirect', function () {
    // The movers page itself: the lists change once a day, so the edge may keep
    // it for an hour, not a month.
    if (!get_query_var('rzmv')) {
        if (is_page() && trailingslashit(get_permalink()) === rzmv_page_url() && !headers_sent()) {
            header('Cache-Control: public, max-age=900, s-maxage=3600');
        }
        return;
    }
    global $wp_query;
    $id = (string)get_query_var('rzmv_id');
    if (get_query_var('rzmv') !== 'card' || !preg_match('/^[0-9a-f]{16}$/', $id)) return;
    $a = rzmv_get('/movers/c/' . $id . '?fragment=1');
    if (is_wp_error($a) || empty($a['html'])) { $wp_query->set_404(); status_header(404); nocache_headers(); return; }
    $meta = $a['meta'];
    $canon = rzmv_card_url($id, (string)($meta['slug'] ?? ''));
    if (trailingslashit(home_url(add_query_arg([], $GLOBALS['wp']->request))) !== $canon) { wp_redirect($canon, 301); exit; }

    status_header(200);
    $wp_query->is_404 = false;
    if (!headers_sent()) header('Cache-Control: public, max-age=900, s-maxage=3600');
    rzmv_strip_seo_head();
    $GLOBALS['rzmv_card'] = $a;
    $GLOBALS['rzmv_canon'] = $canon;
    add_filter('pre_get_document_title', function () use ($meta) { return $meta['title'] . ' — RazMania'; }, PHP_INT_MAX);
    add_action('wp_head', 'rzmv_head_meta', 1);
    add_filter('body_class', function ($c) { return array_merge($c, ['rzmv-route', 'x-full-width-layout-active', 'x-full-width-active']); });
    get_header();
    echo '<div class="x-container max width offset"><div class="x-main full" role="main"><div class="rzmv-wrap">';
    echo '<p class="rzmv-crumb"><a href="' . esc_url(home_url('/')) . '">RazMania</a> › <a href="' . esc_url(rzmv_page_url()) . '">Card Movers</a></p>';
    echo $a['html'];   // server-rendered by our own API; already escaped there
    echo '</div></div></div>';
    get_footer();
    exit;
}, 0);

function rzmv_head_meta() {
    $a = $GLOBALS['rzmv_card']; $m = $a['meta']; $canon = $GLOBALS['rzmv_canon'];
    printf('<title>%s</title>' . "\n", esc_html($m['title'] . ' — RazMania'));
    printf('<link rel="canonical" href="%s">' . "\n", esc_url($canon));
    printf('<meta name="description" content="%s">' . "\n", esc_attr($m['description']));
    // One or two sales is a fact, not a page worth indexing.
    if (!empty($m['noindex'])) echo '<meta name="robots" content="noindex, follow">' . "\n";
    printf('<meta property="og:type" content="website"><meta property="og:title" content="%s"><meta property="og:description" content="%s"><meta property="og:url" content="%s">' . "\n",
           esc_attr($m['title']), esc_attr($m['description']), esc_url($canon));
    if (!empty($m['image'])) printf('<meta property="og:image" content="%s"><meta name="twitter:card" content="summary_large_image">' . "\n", esc_url($m['image']));
    foreach ((array)($m['jsonld'] ?? []) as $ld) {
        // The API printed its own URL scheme; the canonical and the page are ours.
        if (($ld['@type'] ?? '') === 'BreadcrumbList' && !empty($ld['itemListElement'])) {
            $ld['itemListElement'][0]['item'] = home_url('/');
            if (isset($ld['itemListElement'][1])) $ld['itemListElement'][1]['item'] = rzmv_page_url();
            if (isset($ld['itemListElement'][2])) $ld['itemListElement'][2]['item'] = $canon;
        }
        echo '<script type="application/ld+json">' . wp_json_encode($ld, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . '</script>' . "\n";
    }
    echo rzmv_css($a['css']) . "\n";
    echo '<style>.rzmv-crumb{font-size:13px;color:#6E6862;margin:18px auto 0;max-width:1080px;padding:0 20px}.rzmv-crumb a{color:inherit;text-decoration:none}</style>' . "\n";
}

/** Same reason as the Firsts and exhibitors plugins: these URLs resolve as the
 *  blog index, so Rank Math would print the News page's title and Open Graph first. */
function rzmv_strip_seo_head() {
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
