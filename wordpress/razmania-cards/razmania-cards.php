<?php
/**
 * Plugin Name: RazMania Card Data
 * Description: Renders eBay card-sales data from the RazMania API. Output is rendered SERVER-SIDE so Google indexes the numbers — that is the whole SEO thesis.
 * Version:     1.0.0
 * Author:      RazMania
 */

if (!defined('ABSPATH')) exit;

define('RZM_CACHE_TTL', 30 * MINUTE_IN_SECONDS);

/* ------------------------------------------------------------------ config */

add_action('admin_menu', function () {
    add_options_page('RazMania Cards', 'RazMania Cards', 'manage_options', 'rzm-cards', 'rzm_settings_page');
});
add_action('admin_init', function () {
    register_setting('rzm', 'rzm_api_base');
    register_setting('rzm', 'rzm_api_key');
});
function rzm_settings_page() { ?>
    <div class="wrap"><h1>RazMania Card Data</h1>
    <form method="post" action="options.php"><?php settings_fields('rzm'); ?>
      <table class="form-table">
        <tr><th>API base URL</th><td>
          <input type="url" name="rzm_api_base" value="<?php echo esc_attr(get_option('rzm_api_base', 'https://razmania-cards-api.onrender.com')); ?>" class="regular-text">
          <p class="description">No trailing slash. e.g. https://razmania-cards-api.onrender.com</p></td></tr>
        <tr><th>API key</th><td>
          <input type="text" name="rzm_api_key" value="<?php echo esc_attr(get_option('rzm_api_key', '')); ?>" class="regular-text">
          <p class="description">Sent as <code>x-api-key</code>. Leave blank if the API is open.</p></td></tr>
      </table><?php submit_button(); ?>
      <h2>Shortcodes</h2>
      <pre>[razmania_stats]
[razmania_verticals]
[razmania_leaderboard limit="25" vertical="Pokemon" title="Biggest Pokémon sales this week"]
[razmania_player slug="michael-jordan"]</pre>
      <p><a href="<?php echo esc_url(admin_url('options-general.php?page=rzm-cards&rzm_flush=1')); ?>">Clear cached API responses</a></p>
    </form></div>
<?php }

add_action('admin_init', function () {
    if (!empty($_GET['rzm_flush']) && current_user_can('manage_options')) {
        global $wpdb;
        $wpdb->query("DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient%rzm_%'");
    }
});

/* ------------------------------------------------------------------- fetch */
/**
 * GET an API path, cached in a transient.
 *
 * Caching here is not an optimisation, it is a requirement: GoDaddy shared
 * hosting will happily make one outbound HTTP call per pageview otherwise, and
 * Render's smallest instance will not enjoy that.
 */
function rzm_get($path) {
    $base = rtrim(get_option('rzm_api_base', ''), '/');
    if (!$base) return new WP_Error('rzm', 'API base URL not set');

    $key = 'rzm_' . md5($path);
    $hit = get_transient($key);
    if ($hit !== false) return $hit;

    $args = ['timeout' => 8, 'headers' => ['Accept' => 'application/json']];
    if ($k = get_option('rzm_api_key')) $args['headers']['x-api-key'] = $k;

    $res = wp_remote_get($base . $path, $args);
    if (is_wp_error($res)) {
        // Serve the last good copy rather than an error page.
        $stale = get_transient($key . '_stale');
        return $stale !== false ? $stale : $res;
    }
    if (wp_remote_retrieve_response_code($res) !== 200) {
        $stale = get_transient($key . '_stale');
        return $stale !== false ? $stale : new WP_Error('rzm', 'API returned ' . wp_remote_retrieve_response_code($res));
    }
    $data = json_decode(wp_remote_retrieve_body($res), true);
    set_transient($key, $data, RZM_CACHE_TTL);
    set_transient($key . '_stale', $data, WEEK_IN_SECONDS);
    return $data;
}

function rzm_money($n) {
    if ($n === null || $n === '') return '—';
    return '$' . number_format((float)$n, (float)$n < 100 ? 2 : 0);
}
function rzm_err($e) {
    return '<p class="rzm-error">Card data is temporarily unavailable.<!-- ' . esc_html($e->get_error_message()) . ' --></p>';
}

/* -------------------------------------------------------------- shortcodes */

add_shortcode('razmania_stats', function () {
    $d = rzm_get('/v1/stats');
    if (is_wp_error($d)) return rzm_err($d);
    ob_start(); ?>
    <div class="rzm-stats">
      <div><span><?php echo number_format($d['confirmed_sales']); ?></span>confirmed sales tracked</div>
      <div><span><?php echo rzm_money($d['total_gmv']); ?></span>total value</div>
      <div><span><?php echo rzm_money($d['biggest_sale']); ?></span>biggest single sale</div>
      <div><span><?php echo number_format($d['players_tracked']); ?></span>players tracked</div>
    </div>
    <p class="rzm-asof">Data through <?php echo esc_html($d['last_date']); ?>. Confirmed sales only — best-offer listings excluded.</p>
    <?php return ob_get_clean();
});

add_shortcode('razmania_verticals', function () {
    $d = rzm_get('/v1/verticals');
    if (is_wp_error($d)) return rzm_err($d);
    ob_start(); ?>
    <table class="rzm-table">
      <thead><tr><th>Category</th><th>Sales</th><th>Total value</th><th>Median</th><th>Biggest</th><th>WoW</th></tr></thead>
      <tbody>
      <?php foreach ($d['verticals'] as $v):
        $chg = $v['gmv_pct_change']; ?>
        <tr>
          <td><?php echo esc_html($v['vertical']); ?></td>
          <td class="n"><?php echo number_format($v['sales']); ?></td>
          <td class="n"><?php echo rzm_money($v['gmv']); ?></td>
          <td class="n"><?php echo rzm_money($v['median_price']); ?></td>
          <td class="n"><?php echo rzm_money($v['top_sale'] ?? null); ?></td>
          <td class="n <?php echo $chg === null ? '' : ($chg >= 0 ? 'up' : 'down'); ?>">
            <?php echo $chg === null ? '—' : ($chg >= 0 ? '+' : '') . $chg . '%'; ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
    <?php return ob_get_clean();
});

add_shortcode('razmania_leaderboard', function ($a) {
    $a = shortcode_atts(['limit' => 25, 'vertical' => '', 'title' => '', 'images' => 'yes'], $a);
    $path = '/v1/leaderboard?limit=' . (int)$a['limit'];
    if ($a['vertical']) $path .= '&vertical=' . rawurlencode($a['vertical']);
    $d = rzm_get($path);
    if (is_wp_error($d)) return rzm_err($d);

    // ItemList schema so the leaderboard is eligible for rich results.
    $ld = ['@context' => 'https://schema.org', '@type' => 'ItemList', 'itemListElement' => []];
    foreach ($d['results'] as $i => $r) {
        $ld['itemListElement'][] = ['@type' => 'ListItem', 'position' => $i + 1, 'name' => $r['title'], 'url' => $r['url']];
    }
    ob_start();
    if ($a['title']) echo '<h2>' . esc_html($a['title']) . '</h2>'; ?>
    <script type="application/ld+json"><?php echo wp_json_encode($ld); ?></script>
    <table class="rzm-table rzm-leaderboard">
      <thead><tr><th>#</th><?php if ($a['images'] === 'yes') echo '<th></th>'; ?><th>Card</th><th>Category</th><th>Price</th><th>Sold</th><th>Format</th></tr></thead>
      <tbody>
      <?php foreach ($d['results'] as $i => $r): ?>
        <tr>
          <td class="n"><?php echo $a['vertical'] ? $r['rank_in_vertical'] : $r['rank']; ?></td>
          <?php if ($a['images'] === 'yes'): ?>
          <td><?php if ($r['image_url']): ?><img loading="lazy" src="<?php echo esc_url($r['image_url']); ?>" alt="<?php echo esc_attr($r['title']); ?>" width="44"><?php endif; ?></td>
          <?php endif; ?>
          <td><a href="<?php echo esc_url($r['url']); ?>" rel="nofollow noopener" target="_blank"><?php echo esc_html($r['title']); ?></a></td>
          <td><?php echo esc_html($r['vertical']); ?></td>
          <td class="n"><strong><?php echo rzm_money($r['total_price']); ?></strong></td>
          <td class="n"><?php echo esc_html($r['sold_date']); ?></td>
          <td><?php echo esc_html($r['listing_format']); echo $r['bids'] ? ' (' . (int)$r['bids'] . ' bids)' : ''; ?></td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
    <p class="rzm-asof">Confirmed sales only. Best-offer-accepted listings are excluded because eBay publishes the asking price on those, not the amount paid.</p>
    <?php return ob_get_clean();
});

add_shortcode('razmania_player', function ($a) {
    $a = shortcode_atts(['slug' => ''], $a);
    if (!$a['slug']) return '';
    $d = rzm_get('/v1/players/' . rawurlencode($a['slug']));
    if (is_wp_error($d)) return rzm_err($d);
    $p = $d['player'];
    ob_start(); ?>
    <div class="rzm-player">
      <div class="rzm-stats">
        <div><span><?php echo rzm_money($p['top_sale']); ?></span>top sale</div>
        <div><span><?php echo rzm_money($p['median_price']); ?></span>median</div>
        <div><span><?php echo number_format($p['sales']); ?></span>sales tracked</div>
      </div>
      <?php if ($d['comps']): ?>
      <h3>Value by card and grade</h3>
      <table class="rzm-table">
        <thead><tr><th>Card</th><th>Grade</th><th>Median</th><th>Typical range</th><th>Sales</th></tr></thead>
        <tbody>
        <?php foreach ($d['comps'] as $c):
          $label = trim(($c['card_year'] ?: '') . ' ' . ($c['brand_set'] ?: '') . ' ' . ($c['card_number'] ? '#' . $c['card_number'] : '') . ' ' . ($c['parallel'] ?: '')); ?>
          <tr>
            <td><?php echo esc_html($label ?: 'Unattributed'); ?></td>
            <td><?php echo esc_html($c['grade_label']); ?></td>
            <td class="n"><strong><?php echo rzm_money($c['median_price']); ?></strong></td>
            <td class="n"><?php echo rzm_money($c['p25']) . '–' . rzm_money($c['p75']); ?></td>
            <td class="n"><?php echo (int)$c['sales']; ?></td>
          </tr>
        <?php endforeach; ?>
        </tbody>
      </table>
      <p class="rzm-asof">Every row is backed by at least 3 confirmed sales. Range is the middle half of sales.</p>
      <?php endif; ?>
    </div>
    <?php return ob_get_clean();
});

/* ------------------------------------------------- proxy for client-side JS */
/**
 * Lets front-end JS do interactive filtering/sorting without ever seeing the
 * API key, and without CORS. Cached the same way as server-side calls.
 */
add_action('rest_api_init', function () {
    register_rest_route('razmania/v1', '/(?P<path>[a-z0-9_\-/]+)', [
        'methods'  => 'GET',
        'permission_callback' => '__return_true',
        'callback' => function ($req) {
            $allow = ['leaderboard', 'verticals', 'stats', 'daily', 'players', 'comps', 'search', 'sales'];
            $path  = $req['path'];
            $root  = explode('/', $path)[0];
            if (!in_array($root, $allow, true)) return new WP_Error('rzm', 'not allowed', ['status' => 403]);
            $qs = $req->get_query_params();
            unset($qs['path']);
            $d = rzm_get('/v1/' . $path . ($qs ? '?' . http_build_query($qs) : ''));
            if (is_wp_error($d)) return new WP_Error('rzm', 'upstream unavailable', ['status' => 502]);
            return rest_ensure_response($d);
        },
    ]);
});

/* ------------------------------------------------------------------ styles */
add_action('wp_enqueue_scripts', function () {
    wp_register_style('rzm', false);
    wp_enqueue_style('rzm');
    wp_add_inline_style('rzm', '
    .rzm-table{width:100%;border-collapse:collapse;font-size:15px;margin:16px 0}
    .rzm-table th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#5d6470;border-bottom:2px solid #e4e7ec;padding:8px}
    .rzm-table td{padding:9px 8px;border-bottom:1px solid #eef0f3;vertical-align:middle}
    .rzm-table td.n{text-align:right;font-variant-numeric:tabular-nums}
    .rzm-table td.up{color:#0a7d33}.rzm-table td.down{color:#c0392b}
    .rzm-table img{border-radius:4px;display:block}
    .rzm-leaderboard tr:nth-child(even){background:#fafbfc}
    .rzm-stats{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}
    .rzm-stats div{flex:1 1 150px;background:#f7f8fa;border:1px solid #e4e7ec;border-radius:10px;padding:14px 16px;font-size:12px;color:#5d6470}
    .rzm-stats span{display:block;font-size:24px;font-weight:700;color:#12141a;font-variant-numeric:tabular-nums}
    .rzm-asof{font-size:12px;color:#5d6470;font-style:italic}
    .rzm-error{font-size:14px;color:#5d6470}
    ');
});
