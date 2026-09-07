<?php
/**
 * Plugin Name: RazMania Exhibitors
 * Description: The exhibitor directory and every exhibitor profile, served at /exhibitors/ and /exhibitors/<slug>/ from data/profiles.json. Rendered SERVER-SIDE so the profiles are indexable - on razmaniasports.com they were painted by JavaScript and Google never saw a word. Supersedes "RazMania Exhibitor Share Meta"; deactivate that one.
 * Version:     1.3.0
 * Author:      RazMania
 */

if (!defined('ABSPATH')) exit;

define('RZX_VERSION', '1.3.0');
define('RZX_DIR', plugin_dir_path(__FILE__));
define('RZX_URL', plugin_dir_url(__FILE__));

/* ------------------------------------------------------------------ config */

add_action('admin_menu', function () {
    add_options_page('RazMania Exhibitors', 'RazMania Exhibitors', 'manage_options', 'rzx', 'rzx_settings_page');
});
add_action('admin_init', function () {
    foreach (['rzx_event_status', 'rzx_event_name', 'rzx_event_dates', 'rzx_venue', 'rzx_address',
              'rzx_tickets_url', 'rzx_og_image', 'rzx_site_name', 'rzx_home_strip', 'rzx_nav'] as $k) {
        register_setting('rzx', $k);
    }
});

function rzx_opt($k, $default = '') {
    $v = get_option($k, '');
    return ($v === '' || $v === false) ? $default : $v;
}
function rzx_event_name()  { return rzx_opt('rzx_event_name', 'RazMania 2026'); }
function rzx_event_dates() { return rzx_opt('rzx_event_dates', 'August 29–30, 2026'); }
function rzx_venue()       { return rzx_opt('rzx_venue', 'UWM Sports Complex'); }
function rzx_address()     { return rzx_opt('rzx_address', '867 S Blvd E, Pontiac, MI 48341'); }
function rzx_tickets_url() { return rzx_opt('rzx_tickets_url', ''); }
function rzx_site_name()   { return rzx_opt('rzx_site_name', 'RazMania Midwest Sports & Trading Card Festival'); }
/** The show has happened: copy reads "exhibited", not "exhibiting". */
function rzx_past()        { return rzx_opt('rzx_event_status', 'past') === 'past'; }

function rzx_settings_page() {
    $meta = rzx_data()['meta'];
    $c = isset($meta['counts']) ? $meta['counts'] : []; ?>
    <div class="wrap"><h1>RazMania Exhibitors</h1>
    <p>Data: <strong><?php echo (int)($c['exhibitors'] ?? 0); ?> exhibitors</strong>
       (<?php echo (int)($c['full_profiles'] ?? 0); ?> full profiles, <?php echo (int)($c['basic_listings'] ?? 0); ?> basic listings),
       built <?php echo esc_html($meta['built_at'] ?? 'unknown'); ?>.
       To change a profile, edit <code>exhibitors/profiles.overrides.json</code> in the repo, run
       <code>build_profiles.py</code>, and re-upload the plugin. Nothing is editable here on purpose:
       the JSON is the one source of truth.</p>
    <form method="post" action="options.php"><?php settings_fields('rzx'); ?>
      <table class="form-table">
        <tr><th>Event status</th><td>
          <select name="rzx_event_status">
            <option value="past" <?php selected(rzx_opt('rzx_event_status', 'past'), 'past'); ?>>Past — "exhibited at"</option>
            <option value="upcoming" <?php selected(rzx_opt('rzx_event_status', 'past'), 'upcoming'); ?>>Upcoming — "exhibiting at", ticket buttons on</option>
          </select></td></tr>
        <tr><th>Event name</th><td><input type="text" name="rzx_event_name" value="<?php echo esc_attr(rzx_opt('rzx_event_name')); ?>" class="regular-text" placeholder="RazMania 2026"></td></tr>
        <tr><th>Event dates</th><td><input type="text" name="rzx_event_dates" value="<?php echo esc_attr(rzx_opt('rzx_event_dates')); ?>" class="regular-text" placeholder="August 29–30, 2026"></td></tr>
        <tr><th>Venue</th><td><input type="text" name="rzx_venue" value="<?php echo esc_attr(rzx_opt('rzx_venue')); ?>" class="regular-text" placeholder="UWM Sports Complex"></td></tr>
        <tr><th>Address</th><td><input type="text" name="rzx_address" value="<?php echo esc_attr(rzx_opt('rzx_address')); ?>" class="regular-text" placeholder="867 S Blvd E, Pontiac, MI 48341"></td></tr>
        <tr><th>Tickets URL</th><td><input type="url" name="rzx_tickets_url" value="<?php echo esc_attr(rzx_opt('rzx_tickets_url')); ?>" class="regular-text" placeholder="https://www.razmaniasports.com/2026/register">
          <p class="description">Blank hides every ticket button. Shown as "next RazMania" while the event status is Past.</p></td></tr>
        <tr><th>Fallback share image</th><td><input type="url" name="rzx_og_image" value="<?php echo esc_attr(rzx_opt('rzx_og_image')); ?>" class="regular-text">
          <p class="description">1200×630 PNG or JPEG for exhibitors with no logo and for the directory itself. Not a .webp and not the site icon — Facebook and LinkedIn render neither reliably.</p></td></tr>
        <tr><th>Site name (og:site_name)</th><td><input type="text" name="rzx_site_name" value="<?php echo esc_attr(rzx_opt('rzx_site_name')); ?>" class="regular-text"></td></tr>
        <tr><th>Top nav item</th><td>
          <select name="rzx_nav">
            <option value="on" <?php selected(rzx_opt('rzx_nav', 'on'), 'on'); ?>>On — "Exhibitors" before "Live Event" in the header</option>
            <option value="off" <?php selected(rzx_opt('rzx_nav', 'on'), 'off'); ?>>Off</option>
          </select></td></tr>
        <tr><th>Homepage strip</th><td>
          <select name="rzx_home_strip">
            <option value="on" <?php selected(rzx_opt('rzx_home_strip', 'on'), 'on'); ?>>On — "The Best of RazMania" strip injected after the event module</option>
            <option value="off" <?php selected(rzx_opt('rzx_home_strip', 'on'), 'off'); ?>>Off</option>
          </select>
          <p class="description">Anywhere else, the shortcode <code>[razmania_best]</code> renders the same strip server-side.</p></td></tr>
      </table><?php submit_button(); ?>
    </form>
    <h2>URLs</h2>
    <ul>
      <li><a href="<?php echo esc_url(home_url('/exhibitors/')); ?>"><?php echo esc_html(home_url('/exhibitors/')); ?></a> — the directory</li>
      <li><code><?php echo esc_html(home_url('/exhibitors/<slug>/')); ?></code> — one profile per exhibitor</li>
      <li><a href="<?php echo esc_url(home_url('/exhibitors/verified/')); ?>"><?php echo esc_html(home_url('/exhibitors/verified/')); ?></a> — what the Verified stamp means</li>
      <li><a href="<?php echo esc_url(home_url('/exhibitors/best/')); ?>"><?php echo esc_html(home_url('/exhibitors/best/')); ?></a> — The Best of RazMania: ten ranked lists, from <code>data/rankings.json</code></li>
    </ul>
    <p>If those 404, open <a href="<?php echo esc_url(admin_url('options-permalink.php')); ?>">Settings → Permalinks</a> and press Save once; that rebuilds the rewrite rules.
       razmania.com's HTML sits in a 31-day Cloudflare cache, so after an upload purge <code>/exhibitors/*</code> there too.</p>
    </div>
<?php }

/* -------------------------------------------------------------------- data */

function rzx_data() {
    static $d = null;
    if ($d === null) {
        $raw = @file_get_contents(RZX_DIR . 'data/profiles.json');
        $d = $raw ? json_decode($raw, true) : null;
        if (!is_array($d) || empty($d['exhibitors'])) $d = ['meta' => [], 'exhibitors' => []];
    }
    return $d;
}
function rzx_all() { return rzx_data()['exhibitors']; }
/** The Best of RazMania lists, built by exhibitors/rankings.py alongside the profiles. */
function rzx_rankings() {
    static $r = null;
    if ($r === null) {
        $raw = @file_get_contents(RZX_DIR . 'data/rankings.json');
        $r = $raw ? json_decode($raw, true) : null;
        if (!is_array($r) || empty($r['lists'])) $r = ['year' => 2026, 'method' => '', 'lists' => []];
    }
    return $r;
}
function rzx_best_url($key = '') { return home_url('/exhibitors/best/') . ($key ? '#' . $key : ''); }
/** The exhibitor's ranked accolades, best first, then by rank. */
function rzx_ranked($e) {
    $out = array_filter($e['accolades'], function ($a) { return in_array($a['key'] ?? '', ['best', 'certified'], true); });
    usort($out, function ($a, $b) { return [$a['key'] !== 'best', $a['rank']] <=> [$b['key'] !== 'best', $b['rank']]; });
    return $out;
}
function rzx_by_slug($slug) {
    foreach (rzx_all() as $e) if ($e['slug'] === $slug) return $e;
    return null;
}
function rzx_url($e = null) {
    return home_url('/exhibitors/' . ($e ? $e['slug'] . '/' : ''));
}
/** Sponsors and partners are listed, but they are not "tables to shop". */
function rzx_is_sponsor($e) {
    return in_array('Sponsors', $e['categories'], true) || $e['level'] === 'Trusted Partner';
}
function rzx_initials($name) {
    $w = preg_split('/\s+/', trim(preg_replace('/[^A-Za-z0-9 ]/', '', $name)));
    return strtoupper(substr($w[0] ?? '', 0, 1) . substr($w[1] ?? '', 0, 1));
}
/** The shop-facing categories, in display order, from the data itself. */
function rzx_categories() {
    $order = ['Sports Trading Cards', 'Pokemon / TCG', 'Memorabilia', 'Merchandise'];
    $seen = [];
    foreach (rzx_all() as $e) foreach ($e['categories'] as $c) $seen[$c] = true;
    $out = [];
    foreach ($order as $c) if (isset($seen[$c])) $out[] = $c;
    foreach (array_keys($seen) as $c) if (!in_array($c, $out, true) && $c !== 'Dealers' && $c !== 'Sponsors') $out[] = $c;
    return $out;
}

/* ----------------------------------------------------------------- routing */

function rzx_add_rules() {
    add_rewrite_rule('^exhibitors/?$', 'index.php?rzx=index', 'top');
    add_rewrite_rule('^exhibitors/verified/?$', 'index.php?rzx=verified', 'top');
    add_rewrite_rule('^exhibitors/best/?$', 'index.php?rzx=best', 'top');
    add_rewrite_rule('^exhibitors-sitemap\.xml$', 'index.php?rzx=sitemap', 'top');
    add_rewrite_rule('^exhibitors/([a-z0-9\-]+)/?$', 'index.php?rzx=profile&rzx_slug=$matches[1]', 'top');
}
add_action('init', 'rzx_add_rules');
add_filter('query_vars', function ($v) { $v[] = 'rzx'; $v[] = 'rzx_slug'; return $v; });

register_activation_hook(__FILE__, function () { rzx_add_rules(); flush_rewrite_rules(); update_option('rzx_rules_version', RZX_VERSION); });
register_deactivation_hook(__FILE__, 'flush_rewrite_rules');
/* Uploading a new zip over the old folder does not re-run activation, so the
   rules are also flushed once per plugin version, after every plugin has
   registered its own on init. */
add_action('wp_loaded', function () {
    if (get_option('rzx_rules_version') !== RZX_VERSION) {
        flush_rewrite_rules(false);
        update_option('rzx_rules_version', RZX_VERSION);
    }
});

/**
 * The live RazMania Cards plugin (v1.12, inc/exhibitors.php) registered its
 * own rules for these URLs first - `rzm_exh` / `rzm_exh_slug` - and answers
 * them on template_redirect at priority 10 from a 54-exhibitor file dated
 * 18 Aug 2026. This runs at priority 0, accepts both sets of query vars, and
 * exits, so that code is never reached and never needs editing.
 */
add_action('template_redirect', function () {
    $route = get_query_var('rzx');
    $slug  = get_query_var('rzx_slug');
    if (!$route && get_query_var('rzm_exh')) {
        $slug  = get_query_var('rzm_exh_slug');
        $route = $slug ? (in_array($slug, ['verified', 'best'], true) ? $slug : 'profile') : 'index';
    }
    if (!$route) return;
    global $wp_query;

    if ($route === 'sitemap') { rzx_render_sitemap(); exit; }

    $e = null;
    if ($route === 'profile') {
        $slug = sanitize_title($slug);
        $redirects = rzx_data()['meta']['redirects'] ?? [];
        if (isset($redirects[$slug])) {
            wp_redirect(home_url('/exhibitors/' . $redirects[$slug] . '/'), 301);
            exit;
        }
        $e = rzx_by_slug($slug);
        if (!$e) { $wp_query->set_404(); status_header(404); nocache_headers(); return; }
    } elseif (!in_array($route, ['index', 'verified', 'best'], true)) {
        return;
    }
    status_header(200);
    $wp_query->is_404 = false;
    // A day, not the site's month: these pages change on every rebuild and
    // nobody should have to purge Cloudflare by hand to ship a correction.
    if (!headers_sent()) header('Cache-Control: public, max-age=86400, s-maxage=86400');

    rzx_strip_seo_head();
    rzx_strip_legacy_hooks();
    $GLOBALS['rzx_route'] = $route;
    $GLOBALS['rzx_current'] = $e;
    add_filter('pre_get_document_title', 'rzx_document_title', PHP_INT_MAX);
    add_action('wp_head', 'rzx_head_meta', 1);
    add_filter('body_class', function ($c) {
        return array_merge($c, ['rzx-route', 'rzx-' . $GLOBALS['rzx_route'], 'x-full-width-layout-active', 'x-full-width-active']);
    });
    rzx_enqueue();

    get_header();
    echo '<div class="x-container max width offset"><div class="x-main full" role="main"><div class="rzx">';
    if ($route === 'index')         echo rzx_render_index();
    elseif ($route === 'verified')  echo rzx_render_verified();
    elseif ($route === 'best')      echo rzx_render_best();
    else                            echo rzx_render_profile($e);
    echo '</div></div></div>';
    get_footer();
    exit;
}, 0);

/**
 * Rank Math's sitemap only knows posts and pages; every URL here is a
 * virtual route, so without this file Google learns of 156 pages by links
 * alone. Thin listings (noindex) are left out so the sitemap and the robots
 * meta never disagree.
 */
function rzx_render_sitemap() {
    status_header(200);
    header('Content-Type: application/xml; charset=UTF-8');
    header('Cache-Control: public, max-age=86400');
    $built = rzx_data()['meta']['built_at'] ?? '';
    $mod = $built ? substr($built, 0, 10) : date('Y-m-d');
    $urls = [[rzx_url(), '0.9'], [rzx_best_url(), '0.9'], [home_url('/exhibitors/verified/'), '0.4']];
    foreach (rzx_all() as $e) {
        if (rzx_noindex($e)) continue;
        $urls[] = [rzx_url($e), rzx_ranked($e) ? '0.8' : '0.6', rzx_asset($e['share_link'] ?? '')];
    }
    echo '<' . '?xml version="1.0" encoding="UTF-8"?' . '>' . "\n";
    echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">' . "\n";
    foreach ($urls as $u) {
        echo '<url><loc>' . esc_url($u[0]) . '</loc><lastmod>' . esc_html($mod) . '</lastmod><priority>' . $u[1] . '</priority>';
        if (!empty($u[2])) echo '<image:image><image:loc>' . esc_url($u[2]) . '</image:loc></image:image>';
        echo "</url>\n";
    }
    echo '</urlset>';
}
add_filter('robots_txt', function ($out) {
    return rtrim($out) . "\nSitemap: " . home_url('/exhibitors-sitemap.xml') . "\n";
});

/**
 * These URLs resolve as the blog index, so Rank Math emits the News page's
 * title, canonical and Open Graph tags before ours - and every scraper takes
 * the first it sees. Remove every wp_head callback it registered; the filter
 * names move between Rank Math versions, the class prefix does not. (Ported
 * from the Exhibitor Share Meta plugin, which this one replaces.)
 */
function rzx_strip_seo_head() {
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
    // Rank Math had already unhooked core's title tag to print its own; with
    // Rank Math gone nobody prints one, so rzx_head_meta() does.
    remove_action('wp_head', '_wp_render_title_tag', 1);
}

/**
 * The old exhibitor code also filters the document title and prints its own
 * Open Graph tags whenever `rzm_exh` is set. Both are closures, so they are
 * found by the file they were declared in rather than by name.
 */
function rzx_strip_legacy_hooks() {
    global $wp_filter;
    foreach (['wp_head', 'pre_get_document_title'] as $hook) {
        if (empty($wp_filter[$hook])) continue;
        foreach ($wp_filter[$hook]->callbacks as $priority => $callbacks) {
            foreach ($callbacks as $cb) {
                $fn = $cb['function'];
                if (!($fn instanceof Closure)) continue;
                try {
                    $file = (new ReflectionFunction($fn))->getFileName();
                } catch (ReflectionException $ex) {
                    continue;
                }
                if ($file && basename($file) === 'exhibitors.php' && strpos($file, 'razmania-cards') !== false) {
                    remove_filter($hook, $fn, $priority);
                }
            }
        }
    }
}

function rzx_document_title($t) {
    $e = $GLOBALS['rzx_current'];
    if ($GLOBALS['rzx_route'] === 'verified') return 'What the RazMania Verified Exhibitor stamp means';
    if ($GLOBALS['rzx_route'] === 'best') return 'The Best of ' . rzx_event_name() . ' - ten ranked lists';
    if (!$e) return 'Exhibitors - ' . rzx_event_name();
    // Search-first: what they sell and where, which is the query a collector
    // types. "614 Rips: Sports Cards & Pokémon in Columbus, Ohio | RazMania 2026 Exhibitor"
    $what = array_slice(array_values(array_filter($e['specialties'], function ($s) {
        return !in_array(strtolower($s), ['buying', 'trades', 'partner', 'dealer', 'collectibles', 'cards'], true);
    })), 0, 2);
    $t = $e['name'];
    if ($what) $t .= ': ' . implode(' & ', $what);
    if ($e['location']) $t .= ' in ' . $e['location'];
    return $t . ' | ' . rzx_event_name() . ' Exhibitor';
}

function rzx_description($e) {
    $bits = array_filter([$e['tagline'], $e['summary']]);
    $where = rzx_event_name() . ', ' . rzx_venue() . ', Pontiac MI' . ($e['table_label'] ? ' (' . $e['table_label'] . ')' : '');
    $tail = ($e['profile'] === 'full' ? 'Profile, ' : '') . 'Instagram' . ($e['instagram'] ? ' @' . $e['instagram'] : '') . ' and links. ' . ucfirst(rzx_past() ? 'exhibited at ' : 'exhibiting at ') . $where . '.';
    $d = trim(implode(' ', $bits) . ' ' . $tail);
    if (mb_strlen($d) > 158) {
        $d = mb_substr($d, 0, 155);
        $d = mb_substr($d, 0, mb_strrpos($d, ' ')) . '…';
    }
    return $d;
}

function rzx_head_meta() {
    $route = $GLOBALS['rzx_route'];
    $e = $GLOBALS['rzx_current'];
    $title = rzx_document_title('');
    if ($route === 'index') {
        $c = rzx_data()['meta']['counts'] ?? [];
        $desc = 'Every exhibitor at ' . rzx_event_name() . ' - ' . (int)($c['exhibitors'] ?? 0) . ' tables at the ' . rzx_venue() . ', with a profile of what each one deals in and how to reach them.';
        $url = rzx_url(); $img = rzx_opt('rzx_og_image');
    } elseif ($route === 'verified') {
        $desc = 'Every business showing the RazMania Verified Exhibitor stamp has a confirmed table and a profile RazMania built and checked. It cannot be bought.';
        $url = home_url('/exhibitors/verified/'); $img = rzx_opt('rzx_og_image');
    } elseif ($route === 'best') {
        $titles = array_map(function ($l) { return $l['title']; }, rzx_rankings()['lists']);
        $desc = 'The tables collectors should hit first at ' . rzx_event_name() . ', ranked ten deep: ' . implode(', ', $titles) . '.';
        $url = rzx_best_url(); $img = rzx_opt('rzx_og_image');
    } else {
        $desc = rzx_description($e);
        $url = rzx_url($e); $img = rzx_asset($e['share_link'] ?? '') ?: ($e['logo'] ?: rzx_opt('rzx_og_image'));
    }
    $card = $e && !empty($e['share_link']);
    echo "\n<!-- RazMania exhibitors -->\n";
    printf('<title>%s</title>' . "\n", esc_html($title));
    if ($e && rzx_noindex($e)) echo '<meta name="robots" content="noindex,follow">' . "\n";
    printf('<link rel="canonical" href="%s">' . "\n", esc_url($url));
    printf('<meta name="description" content="%s">' . "\n", esc_attr($desc));
    printf('<meta property="og:site_name" content="%s">' . "\n", esc_attr(rzx_site_name()));
    printf('<meta property="og:type" content="%s">' . "\n", $e ? 'profile' : 'website');
    printf('<meta property="og:title" content="%s">' . "\n", esc_attr($title));
    printf('<meta property="og:description" content="%s">' . "\n", esc_attr($desc));
    printf('<meta property="og:url" content="%s">' . "\n", esc_url($url));
    printf('<meta name="twitter:card" content="%s">' . "\n", $card ? 'summary_large_image' : 'summary');
    printf('<meta name="twitter:title" content="%s">' . "\n", esc_attr($title));
    printf('<meta name="twitter:description" content="%s">' . "\n", esc_attr($desc));
    if ($img) {
        printf('<meta property="og:image" content="%s">' . "\n", esc_url($img));
        if ($card) echo '<meta property="og:image:width" content="1200">' . "\n" . '<meta property="og:image:height" content="630">' . "\n";
        printf('<meta property="og:image:alt" content="%s">' . "\n", esc_attr($e ? $e['name'] . ' at ' . rzx_event_name() : rzx_event_name() . ' exhibitors'));
        printf('<meta name="twitter:image" content="%s">' . "\n", esc_url($img));
    }
    echo '<script type="application/ld+json">' . wp_json_encode(rzx_ld($route, $e), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . "</script>\n";
}

function rzx_ld($route, $e) {
    $crumbs = [['@type' => 'ListItem', 'position' => 1, 'name' => 'Exhibitors', 'item' => rzx_url()]];
    if ($route === 'index') {
        $items = []; $i = 1;
        foreach (rzx_all() as $x) $items[] = ['@type' => 'ListItem', 'position' => $i++, 'name' => $x['name'], 'url' => rzx_url($x)];
        return ['@context' => 'https://schema.org', '@graph' => [
            ['@type' => 'ItemList', 'name' => rzx_event_name() . ' exhibitors', 'numberOfItems' => count($items), 'itemListElement' => $items],
            ['@type' => 'BreadcrumbList', 'itemListElement' => $crumbs],
        ]];
    }
    if ($route === 'verified') {
        $crumbs[] = ['@type' => 'ListItem', 'position' => 2, 'name' => 'Verified Exhibitor', 'item' => home_url('/exhibitors/verified/')];
        return ['@context' => 'https://schema.org', '@type' => 'BreadcrumbList', 'itemListElement' => $crumbs];
    }
    if ($route === 'best') {
        $crumbs[] = ['@type' => 'ListItem', 'position' => 2, 'name' => 'The Best of ' . rzx_event_name(), 'item' => rzx_best_url()];
        $graph = [['@type' => 'BreadcrumbList', 'itemListElement' => $crumbs]];
        foreach (rzx_rankings()['lists'] as $l) {
            $items = [];
            foreach ($l['entries'] as $x) $items[] = ['@type' => 'ListItem', 'position' => $x['rank'], 'name' => $x['name'], 'url' => home_url('/exhibitors/' . $x['slug'] . '/')];
            $graph[] = ['@type' => 'ItemList', '@id' => rzx_best_url($l['key']), 'name' => $l['title'] . ' - ' . rzx_event_name(),
                        'description' => $l['strapline'], 'itemListOrder' => 'https://schema.org/ItemListOrderAscending',
                        'numberOfItems' => count($items), 'itemListElement' => $items];
        }
        return ['@context' => 'https://schema.org', '@graph' => $graph];
    }
    $crumbs[] = ['@type' => 'ListItem', 'position' => 2, 'name' => $e['name'], 'item' => rzx_url($e)];
    $org = ['@type' => 'Organization', 'name' => $e['name'], 'url' => $e['website'] ?: rzx_url($e), 'description' => rzx_description($e)];
    if ($e['logo']) $org['logo'] = $e['logo'];
    if ($e['instagram']) $org['sameAs'] = ['https://instagram.com/' . $e['instagram']];
    if ($e['location']) $org['areaServed'] = $e['location'];
    return ['@context' => 'https://schema.org', '@graph' => [
        ['@type' => 'ProfilePage', 'url' => rzx_url($e), 'name' => rzx_document_title(''), 'mainEntity' => $org,
         'about' => ['@type' => 'Event', 'name' => rzx_event_name(), 'location' => ['@type' => 'Place', 'name' => rzx_venue(), 'address' => rzx_address()]]],
        ['@type' => 'BreadcrumbList', 'itemListElement' => $crumbs],
    ]];
}

function rzx_enqueue() {
    wp_enqueue_style('rzx', RZX_URL . 'assets/exhibitors.css', [], RZX_VERSION);
    wp_enqueue_script('rzx', RZX_URL . 'assets/exhibitors.js', [], RZX_VERSION, true);
}

/* ------------------------------------------------------------- top nav */

/**
 * "Exhibitors" in the site header, before "Live Event". The header's four
 * main items (Market Data, Analysis, Make Money, Live Event; ids 900-903) are
 * not menu entries at all - the hub plugin injects them through this same
 * filter - so the theme's walker already renders synthetic items with the
 * x-anchor styling. This runs after the hub (priority 20), finds Live Event,
 * and slots one more in front of it. No menu to edit, nothing to be wiped by
 * a builder re-save. If the hub's items are not in this menu, nothing is
 * added: this is the header menu's item, not every menu's.
 */
add_filter('wp_get_nav_menu_items', function ($items, $menu, $args) {
    if (is_admin() || rzx_opt('rzx_nav', 'on') !== 'on' || !is_array($items)) return $items;
    foreach ($items as $it) if (isset($it->title) && $it->title === 'Exhibitors') return $items;
    $at = null;
    foreach ($items as $i => $it) {
        $cls = isset($it->classes) && is_array($it->classes) ? $it->classes : [];
        if ((int)$it->ID === 903 || in_array('rzm-nav-live-event', $cls, true)) { $at = $i; break; }
    }
    if ($at === null) return $items;

    $on = (bool)get_query_var('rzx') || (bool)get_query_var('rzm_exh');
    $n = new stdClass();
    $n->ID = 904; $n->db_id = 904; $n->menu_item_parent = 0; $n->post_parent = 0;
    $n->object_id = 0; $n->object = 'custom'; $n->type = 'custom'; $n->type_label = 'Custom Link';
    $n->title = 'Exhibitors'; $n->post_title = 'Exhibitors'; $n->post_name = 'exhibitors';
    $n->url = rzx_url(); $n->guid = rzx_url(); $n->target = ''; $n->attr_title = 'Every exhibitor, and the Best of RazMania lists';
    $n->description = ''; $n->xfn = ''; $n->post_type = 'nav_menu_item'; $n->post_status = 'publish';
    $n->classes = ['rzm-nav-item', 'rzm-nav-exhibitors', 'menu-item'];
    $n->current = $on; $n->current_item_ancestor = false; $n->current_item_parent = false;
    $n->menu_order = 0;

    array_splice($items, $at, 0, [$n]);
    $order = 1;
    foreach ($items as $it) $it->menu_order = $order++;
    return $items;
}, 20, 3);

/* ---------------------------------------------------------- homepage strip */

/**
 * [razmania_best] - a compact strip: the ten lists with their #1 tables,
 * linking to the Best page. Server-rendered wherever the shortcode is placed.
 * On the homepage it is injected by assets/home.js instead, because that
 * page's story grid is a Cornerstone Looper that any builder re-save wipes
 * (the RazMania Event plugin does the same for the same reason).
 */
function rzx_render_strip() {
    $r = rzx_rankings();
    if (!$r['lists']) return '';
    $h  = '<section class="rzx rzx-strip"><div class="rzx-strip-head"><span class="rzx-eyebrow">' . rzx_h(rzx_event_name()) . ' &middot; ten ranked lists</span>';
    $h .= '<h2 class="rzx-strip-title"><a href="' . esc_url(rzx_best_url()) . '">The Best of ' . rzx_h(rzx_event_name()) . '</a></h2>';
    $h .= '<p class="rzx-strip-dek">The tables to hit first, one list per way of collecting.</p></div><ul class="rzx-strip-list">';
    foreach ($r['lists'] as $l) {
        $top = $l['entries'][0] ?? null;
        if (!$top) continue;
        $h .= '<li><a href="' . esc_url(rzx_best_url($l['key'])) . '"><span class="rzx-strip-cat">' . rzx_h($l['title']) . '</span><span class="rzx-strip-win">&#9733; ' . rzx_h($top['name']) . '</span></a></li>';
    }
    $h .= '</ul><a class="rzx-btn rzx-btn--gold rzx-strip-cta" href="' . esc_url(rzx_best_url()) . '">See all ten lists &rarr;</a></section>';
    return $h;
}
add_shortcode('razmania_best', 'rzx_render_strip');

add_action('wp_enqueue_scripts', function () {
    if (!is_front_page() || rzx_opt('rzx_home_strip', 'on') !== 'on' || !rzx_rankings()['lists']) return;
    wp_enqueue_style('rzx', RZX_URL . 'assets/exhibitors.css', [], RZX_VERSION);
    wp_enqueue_script('rzx-home', RZX_URL . 'assets/home.js', [], RZX_VERSION, true);
    wp_localize_script('rzx-home', 'RZX_HOME', ['markup' => rzx_render_strip()]);
});

/* ---------------------------------------------------------------- partials */

function rzx_h($s) { return esc_html($s); }

/** Local, resized copy first (2 KB WebP); the Swoogo original only as a fallback. */
function rzx_logo_src($e) {
    if (!empty($e['logo_local'])) return RZX_URL . $e['logo_local'];
    return $e['logo'] ?: '';
}
function rzx_asset($rel) { return $rel ? RZX_URL . ltrim($rel, '/') : ''; }
/** A basic listing with nothing written is thin; keep it out of the index until it is. */
function rzx_noindex($e) {
    return $e['profile'] === 'basic' && empty($e['take']) && empty($e['editorial']);
}

function rzx_logo($e, $class = 'rzx-logo') {
    $src = rzx_logo_src($e);
    if ($src) {
        return '<span class="' . $class . '"><img src="' . esc_url($src) . '" alt="' . esc_attr($e['name']) . ' logo" loading="lazy" decoding="async" width="132" height="132"></span>';
    }
    return '<span class="' . $class . ' rzx-logo--initials" aria-hidden="true"><span>' . rzx_h(rzx_initials($e['name'])) . '</span></span>';
}

function rzx_stamp($year = 2026) {
    ob_start(); ?>
    <a class="rzx-stamp" href="<?php echo esc_url(home_url('/exhibitors/verified/')); ?>" title="What the RazMania Verified Exhibitor stamp means">
      <svg viewBox="0 0 200 200" role="img" aria-label="RazMania Verified Exhibitor <?php echo (int)$year; ?>">
        <defs><path id="rzxTop" d="M 26,100 A 74,74 0 0,1 174,100" fill="none"/><path id="rzxBot" d="M 36,100 A 64,64 0 0,0 164,100" fill="none"/></defs>
        <circle cx="100" cy="100" r="96" class="rzx-sfill"/><circle cx="100" cy="100" r="94" class="rzx-sring"/><circle cx="100" cy="100" r="84" class="rzx-sring2"/>
        <text class="rzx-sarc"><textPath href="#rzxTop" startOffset="50%" text-anchor="middle">RAZMANIA VERIFIED</textPath></text>
        <text class="rzx-sarc"><textPath href="#rzxBot" startOffset="50%" text-anchor="middle">EXHIBITOR</textPath></text>
        <text class="rzx-sstar" x="46" y="106" text-anchor="middle">&#9733;</text><text class="rzx-sstar" x="154" y="106" text-anchor="middle">&#9733;</text>
        <text class="rzx-stick" x="100" y="98" text-anchor="middle">&#10003;</text>
        <text class="rzx-syear" x="100" y="126" text-anchor="middle"><?php echo (int)$year; ?></text>
      </svg></a>
    <?php return ob_get_clean();
}

/**
 * Accolades are data, not code: each is {key,label,year,detail?,rank?,category?}.
 * "verified" renders as the stamp in the hero; everything else lands in the
 * accolade strip. Rankings (see exhibitors/RANKINGS.md) arrive the same way,
 * as {key:"rank", category, rank, label} entries, so no template change is
 * needed when they ship.
 */
/**
 * The loud one. A "best" placing is a full-width gold band under the hero;
 * a "certified" placing is the same band in ink. Every band links to its
 * list, so the claim is one tap from its evidence.
 */
function rzx_banners($e) {
    $h = '';
    foreach (rzx_ranked($e) as $a) {
        $best = $a['key'] === 'best';
        $h .= '<a class="rzx-banner ' . ($best ? 'rzx-banner--best' : 'rzx-banner--cert') . '" href="' . esc_url(rzx_best_url($a['list_key'] ?? '')) . '">'
            . '<span class="rzx-banner-mark">' . ($best ? '&#9733;' : '&#10003;') . '</span>'
            . '<span class="rzx-banner-text"><b>' . rzx_h($best ? 'Best of ' . rzx_event_name() : 'RazMania Certified') . '</b>'
            . '<span>#' . (int)$a['rank'] . ' of ' . (int)($a['of'] ?? 10) . ' &middot; ' . rzx_h($a['list'] ?? '') . '</span></span>'
            . '<span class="rzx-banner-cta">See the list &rarr;</span></a>';
    }
    return $h ? '<div class="rzx-banners">' . $h . '</div>' : '';
}

/** Small mark for a directory card: the exhibitor's top placing. */
function rzx_card_badge($e) {
    $r = rzx_ranked($e);
    if (!$r) return '';
    $a = reset($r);
    $best = $a['key'] === 'best';
    return '<span class="rzx-badge ' . ($best ? 'rzx-badge--best' : 'rzx-badge--cert') . '" title="' . esc_attr('#' . (int)$a['rank'] . ' ' . ($a['list'] ?? '')) . '">'
        . ($best ? '&#9733; Best #' . (int)$a['rank'] : '&#10003; Certified') . '</span>';
}

function rzx_accolades($e) {
    $rows = array_filter($e['accolades'], function ($a) { return !in_array($a['key'] ?? '', ['verified', 'best', 'certified'], true); });
    if (!$rows) return '';
    $h = '<ul class="rzx-accolades">';
    foreach ($rows as $a) {
        $h .= '<li class="rzx-acc rzx-acc--' . esc_attr($a['key'] ?? 'award') . '">';
        if (!empty($a['rank'])) $h .= '<b class="rzx-acc-rank">#' . (int)$a['rank'] . '</b>';
        $h .= '<span class="rzx-acc-label">' . rzx_h($a['label'] ?? '') . '</span>';
        if (!empty($a['category'])) $h .= '<span class="rzx-acc-cat">' . rzx_h($a['category']) . '</span>';
        if (!empty($a['detail'])) $h .= '<span class="rzx-acc-detail">' . rzx_h($a['detail']) . '</span>';
        $h .= '</li>';
    }
    return $h . '</ul>';
}

function rzx_ticket_btn($class = 'rzx-btn') {
    $u = rzx_tickets_url();
    if (!$u) return '';
    $label = rzx_past() ? 'Tickets for the next RazMania' : 'Get RazMania tickets';
    return '<a class="' . $class . '" data-t="tickets" href="' . esc_url($u) . '">' . $label . '</a>';
}

function rzx_card($e) {
    $line = $e['tagline'] ?: ($e['summary'] ?: implode(' · ', array_diff($e['categories'], ['Dealers'])));
    $meta = array_filter([$e['location'], $e['table_label']]);
    // What the directory search matches. Name, place and what they carry;
    // not the prose, which doubled the page weight for little recall.
    $text = strtolower(implode(' ', array_unique(array_filter([$e['name'], $e['location'], implode(' ', $e['specialties']), implode(' ', array_diff($e['categories'], ['Dealers'])), $e['instagram']]))));
    $h  = '<a class="rzx-card' . ($e['profile'] === 'full' ? ' rzx-card--full' : '') . '" href="' . esc_url(rzx_url($e)) . '" data-cats="' . esc_attr(implode('|', $e['categories'])) . '" data-text="' . esc_attr($text) . '">';
    $h .= rzx_logo($e, 'rzx-card-logo');
    $h .= '<span class="rzx-card-body"><span class="rzx-card-name">' . rzx_h($e['name']) . '<i class="rzx-tick" title="RazMania Verified Exhibitor">&#10003;</i>' . rzx_card_badge($e) . '</span>';
    if ($line) $h .= '<span class="rzx-card-line">' . rzx_h($line) . '</span>';
    if ($meta) $h .= '<span class="rzx-card-meta">' . rzx_h(implode(' · ', $meta)) . '</span>';
    $h .= '<span class="rzx-card-cta">' . ($e['profile'] === 'full' ? 'Read their profile' : 'View listing') . ' &rarr;</span>';
    return $h . '</span></a>';
}

/* --------------------------------------------------------------- directory */

function rzx_render_index() {
    $all = rzx_all();
    $tables = array_filter($all, function ($e) { return !rzx_is_sponsor($e); });
    $sponsors = array_filter($all, 'rzx_is_sponsor');
    $full = count(array_filter($all, function ($e) { return $e['profile'] === 'full'; }));
    $verb = rzx_past() ? 'had' : 'has';
    ob_start(); ?>
    <article class="rzx-page rzx-index">
      <header class="rzx-mast">
        <span class="rzx-eyebrow"><?php echo rzx_h(rzx_event_name()); ?> &middot; <?php echo rzx_h(rzx_event_dates()); ?></span>
        <h1 class="rzx-h1">Meet the floor</h1>
        <p class="rzx-dek">Every exhibitor below <?php echo $verb; ?> a confirmed table at the <?php echo rzx_h(rzx_venue()); ?>, and <?php echo (int)$full; ?> of them have a profile we wrote and checked: what they deal in, where they are from, and how to reach them.
          <a class="rzx-vlink" href="<?php echo esc_url(home_url('/exhibitors/verified/')); ?>"><i class="rzx-tick">&#10003;</i> What the Verified Exhibitor stamp means</a></p>
        <?php if (rzx_rankings()['lists']): ?>
        <a class="rzx-bestlink" href="<?php echo esc_url(rzx_best_url()); ?>"><span class="rzx-bestlink-star">&#9733;</span><b>The Best of <?php echo rzx_h(rzx_event_name()); ?></b><span>Ten ranked lists: the tables to hit first &rarr;</span></a>
        <?php endif; ?>
      </header>

      <div class="rzx-filters" data-filters>
        <label class="rzx-search"><span class="screen-reader-text">Search exhibitors</span>
          <input type="search" placeholder="Search by name, city or what they carry" data-search autocomplete="off"></label>
        <div class="rzx-chips" role="group" aria-label="Filter by category">
          <button type="button" class="rzx-chip is-on" data-cat="">All <b><?php echo count($tables); ?></b></button>
          <?php foreach (rzx_categories() as $c):
            $n = count(array_filter($tables, function ($e) use ($c) { return in_array($c, $e['categories'], true); })); ?>
          <button type="button" class="rzx-chip" data-cat="<?php echo esc_attr($c); ?>"><?php echo rzx_h($c); ?> <b><?php echo $n; ?></b></button>
          <?php endforeach; ?>
        </div>
        <p class="rzx-count" data-count hidden></p>
      </div>

      <section class="rzx-grid" data-grid>
        <?php foreach ($tables as $e) echo rzx_card($e); ?>
      </section>
      <p class="rzx-empty" data-empty hidden>Nothing matches that. Try a shorter word, or clear the filter.</p>

      <?php if ($sponsors): ?>
      <section class="rzx-sponsors">
        <div class="rzx-lab"><h2>Sponsors &amp; partners</h2><i></i></div>
        <div class="rzx-grid rzx-grid--sponsors">
          <?php foreach ($sponsors as $e) echo rzx_card($e); ?>
        </div>
      </section>
      <?php endif; ?>

      <footer class="rzx-foot">
        <p><?php echo rzx_h(rzx_event_dates()); ?> &middot; <?php echo rzx_h(rzx_venue()); ?>, <?php echo rzx_h(rzx_address()); ?>. <?php echo rzx_ticket_btn('rzx-textlink'); ?></p>
        <p class="rzx-prov"><b>How these profiles were made.</b> Compiled by RazMania from each exhibitor's registration and their public Instagram, and reviewed before publishing. Spot something wrong on your page? Email us and we will fix it the same day.</p>
      </footer>
    </article>
    <?php return ob_get_clean();
}

/* ----------------------------------------------------------------- profile */

function rzx_related($e, $n = 3) {
    $mine = array_diff($e['categories'], ['Dealers']);
    $pool = [];
    foreach (rzx_all() as $x) {
        if ($x['id'] === $e['id'] || rzx_is_sponsor($x)) continue;
        $score = count(array_intersect($mine, $x['categories'])) * 2 + ($x['profile'] === 'full' ? 1 : 0);
        if ($x['location'] && $x['location'] === $e['location']) $score += 1;
        $pool[] = [$score, $x];
    }
    // Deterministic: same three neighbours on every render, so the cached
    // page and a fresh one never disagree.
    usort($pool, function ($a, $b) { return ($b[0] <=> $a[0]) ?: strcmp($a[1]['name'], $b[1]['name']); });
    // Rotate through the best twelve by the exhibitor's own id, so neighbouring
    // profiles do not all recommend the same three tables.
    $top = array_slice($pool, 0, 12);
    if (!$top) return [];
    $rot = ((int)$e['id']) % count($top);
    $out = [];
    for ($i = 0; $i < count($top) && count($out) < $n; $i++) $out[] = $top[($i + $rot) % count($top)][1];
    return $out;
}

/**
 * The hand-written sections were authored against the Swoogo snippet's
 * class names (rzm-*). They are stored verbatim so the writing survives; the
 * prefix is swapped so this plugin's stylesheet owns them.
 */
function rzx_sections($e) {
    $h = '';
    foreach ($e['sections_html'] as $html) {
        // Drop the runtime-only markers first, then move the prefix; the
        // order matters because 'rzm-only' contains 'rzm-'.
        $html = str_replace(['rzm-only', 'rzm-dsp', 'rzm-'], ['', '', 'rzx-'], $html);
        $html = preg_replace('/\s+data-only="\d+"/', '', $html);
        $h .= $html;
    }
    return $h;
}

function rzx_render_profile($e) {
    $past = rzx_past();
    $ig = $e['instagram'] ? 'https://instagram.com/' . rawurlencode($e['instagram']) : '';
    $best = implode(' · ', $e['specialties']);
    $by = 'Written by RazMania from ' . $e['name'] . '\'s exhibitor registration and ' . ($e['instagram'] ? 'their public Instagram' : 'publicly available information about their business') . ', and reviewed before publishing.';
    ob_start(); ?>
    <article class="rzx-page rzx-profile" data-profile data-name="<?php echo esc_attr($e['name']); ?>">
      <nav class="rzx-crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(rzx_url()); ?>">Exhibitors</a> <span>/</span> <span aria-current="page"><?php echo rzx_h($e['name']); ?></span></nav>

      <header class="rzx-hero">
        <div class="rzx-hero-left"><?php echo rzx_logo($e); echo rzx_stamp(); ?></div>
        <div class="rzx-hero-main">
          <span class="rzx-eyebrow"><?php echo $past ? 'Exhibited at ' : 'Exhibiting at '; echo rzx_h(rzx_event_name()); ?></span>
          <h1 class="rzx-h1 rzx-name"><?php echo rzx_h($e['name']); ?></h1>
          <?php if ($e['tagline']): ?><p class="rzx-tag"><?php echo rzx_h($e['tagline']); ?></p><?php endif; ?>
          <?php if ($e['summary']): ?><p class="rzx-sub"><?php echo rzx_h($e['summary']); ?></p><?php endif; ?>
          <?php if ($e['chips']): ?><p class="rzx-idchips"><?php foreach ($e['chips'] as $c): ?><span class="rzx-idchip"><i><?php echo rzx_h($c['icon']); ?></i><?php echo rzx_h($c['label']); ?></span><?php endforeach; ?></p><?php endif; ?>
          <?php echo rzx_accolades($e); ?>
          <div class="rzx-ctas">
            <?php if ($ig): ?><a class="rzx-btn rzx-btn--gold" data-t="ig_follow" href="<?php echo esc_url($ig); ?>" target="_blank" rel="nofollow noopener">Follow @<?php echo rzx_h($e['instagram']); ?></a><?php endif; ?>
            <?php if ($e['website']): ?><a class="rzx-btn" data-t="shop" href="<?php echo esc_url($e['website']); ?>" target="_blank" rel="nofollow noopener">Visit their shop</a><?php endif; ?>
            <button class="rzx-btn" type="button" data-share>Share this profile</button>
            <?php if (!$ig && !$e['website']) echo rzx_ticket_btn(); ?>
          </div>
        </div>
        <aside class="rzx-intel">
          <b class="rzx-intelttl">Quick intel</b>
          <?php if ($best): ?><span class="rzx-irow"><span class="rzx-ik">Best for</span><span class="rzx-iv"><?php echo rzx_h($best); ?></span></span><?php endif; ?>
          <?php if ($e['location']): ?><span class="rzx-irow"><span class="rzx-ik">Based in</span><span class="rzx-iv"><?php echo rzx_h($e['location']); ?></span></span><?php endif; ?>
          <?php if ($e['instagram']): ?><span class="rzx-irow"><span class="rzx-ik">Instagram</span><span class="rzx-iv">@<?php echo rzx_h($e['instagram']); ?><?php if (!empty($e['ig_followers'])): ?> <small><?php echo number_format((int)$e['ig_followers']); ?> followers<?php echo !empty($e['ig_posts']) ? ' · ' . number_format((int)$e['ig_posts']) . ' posts' : ''; ?></small><?php endif; ?></span></span><?php endif; ?>
          <span class="rzx-irow"><span class="rzx-ik">At <?php echo rzx_h(rzx_event_name()); ?></span><span class="rzx-iv"><?php echo rzx_h($e['days']); ?></span></span>
          <?php if ($e['table_label']): ?><span class="rzx-irow"><span class="rzx-ik">Table</span><span class="rzx-iv"><?php echo rzx_h($e['table_label']); ?></span></span><?php endif; ?>
          <span class="rzx-irow"><span class="rzx-ik">Venue</span><span class="rzx-iv"><?php echo rzx_h(rzx_venue()); ?>, Pontiac</span></span>
        </aside>
      </header>

      <?php echo rzx_banners($e); ?>

      <?php if (!empty($e['share_link'])): $story = rzx_asset($e['share_story'] ?? ''); $link = rzx_asset($e['share_link']); $top = rzx_ranked($e); $top = $top ? reset($top) : null;
        $caption = $e['name'] . ($top ? ' is #' . (int)$top['rank'] . ' in ' . $top['list'] . ' on the Best of ' . rzx_event_name() . ' list.' : ' ' . (rzx_past() ? 'exhibited' : 'is exhibiting') . ' at ' . rzx_event_name() . '.') . ' Full profile: ' . rzx_url($e); ?>
      <section class="rzx-kit">
        <a class="rzx-kit-thumb" href="<?php echo esc_url($story ?: $link); ?>" download><img src="<?php echo esc_url($story ?: $link); ?>" alt="<?php echo esc_attr($e['name']); ?> share graphic" loading="lazy" decoding="async"></a>
        <div class="rzx-kit-body">
          <span class="rzx-eyebrow">Share kit</span>
          <h2 class="rzx-kit-title"><?php echo $top ? 'Your placing, ready to post.' : 'Your page, ready to post.'; ?></h2>
          <p><?php echo $story ? 'A story-sized graphic and a link card, both made for ' : 'A link card made for '; ?><?php echo rzx_h($e['name']); ?>. Download, post, tag <b>@razmaniasports</b>.</p>
          <div class="rzx-ctas">
            <?php if ($story): ?><a class="rzx-btn rzx-btn--gold" data-t="kit_story" href="<?php echo esc_url($story); ?>" download>Download story graphic</a><?php endif; ?>
            <a class="rzx-btn<?php echo $story ? '' : ' rzx-btn--gold'; ?>" data-t="kit_link" href="<?php echo esc_url($link); ?>" download>Download link card</a>
            <button class="rzx-btn" type="button" data-copycap>Copy caption</button>
          </div>
          <p class="rzx-caption" data-caption><?php echo rzx_h($caption); ?></p>
          <div class="rzx-toast" data-kittoast aria-live="polite"></div>
        </div>
      </section>
      <?php endif; ?>

      <div class="rzx-body">
        <?php if ($e['editorial']): ?>
        <section class="rzx-box rzx-s8 rzx-ed">
          <span class="rzx-edbadge">&#10022; RazMania Editorial</span>
          <div class="rzx-lab"><h2>Why they&rsquo;re on our radar</h2><i></i></div>
          <p class="lead"><?php echo rzx_h($e['editorial']); ?></p>
          <p class="rzx-byline"><?php echo rzx_h($by); ?></p>
        </section>
        <?php elseif (!empty($e['take'])): ?>
        <section class="rzx-box rzx-s8 rzx-ed">
          <span class="rzx-edbadge">&#10022; RazMania Editorial</span>
          <div class="rzx-lab"><h2>Our take</h2><i></i></div>
          <?php foreach ($e['take'] as $i => $p): ?><p class="<?php echo $i === 0 ? 'lead' : ''; ?>"><?php echo rzx_h($p); ?></p><?php endforeach; ?>
          <p class="rzx-byline"><?php echo rzx_h($by); ?><?php echo !empty($e['take_date']) ? ' ' . rzx_h($e['take_date']) . '.' : ''; ?></p>
        </section>
        <?php elseif ($e['profile'] === 'basic'): ?>
        <section class="rzx-box rzx-s8 rzx-ed rzx-ed--basic">
          <div class="rzx-lab"><h2>About this listing</h2><i></i></div>
          <p class="lead"><?php echo rzx_h($e['name']); ?> <?php echo $past ? 'had' : 'has'; ?> a confirmed table at <?php echo rzx_h(rzx_event_name()); ?><?php echo $e['table_label'] ? ' (' . rzx_h($e['table_label']) . ')' : ''; ?><?php echo $e['categories'] ? ', listed under ' . rzx_h(implode(' and ', array_diff($e['categories'], ['Dealers']) ?: $e['categories'])) : ''; ?>. We have not written their full profile yet.</p>
          <p class="rzx-byline">Exhibiting with us and want your page written up? Reply to any RazMania email with your Instagram and what you carry, and we will do the rest.</p>
        </section>
        <?php endif; ?>

        <?php echo rzx_sections($e); ?>

        <?php if (!empty($e['facts'])): ?>
        <section class="rzx-box rzx-s8 rzx-factsbox">
          <div class="rzx-lab"><h2>At the table</h2><i></i><span class="rzx-lab-note">Confirmed by the exhibitor</span></div>
          <dl class="rzx-facts"><?php foreach ($e['facts'] as $f): ?><div><dt><?php echo rzx_h($f['k']); ?></dt><dd><?php echo rzx_h($f['v']); ?></dd></div><?php endforeach; ?></dl>
        </section>
        <?php endif; ?>

        <aside class="rzx-box rzx-s4 rzx-find">
          <b class="rzx-intelttl"><?php echo $past ? 'Where they were' : 'Find them at ' . rzx_h(rzx_event_name()); ?></b>
          <p class="rzx-findwhen"><?php echo rzx_h(rzx_event_dates()); ?></p>
          <p class="rzx-findloc"><?php echo rzx_h(rzx_venue()); ?><br><?php echo rzx_h(rzx_address()); ?><?php echo $e['table_label'] ? '<br>' . rzx_h($e['table_label']) : ''; ?></p>
          <?php echo rzx_ticket_btn('rzx-btn rzx-btn--gold rzx-btn--wide'); ?>
          <?php if ($ig): ?><a class="rzx-btn rzx-btn--wide" data-t="ig_follow_end" target="_blank" rel="nofollow noopener" href="<?php echo esc_url($ig); ?>">Follow @<?php echo rzx_h($e['instagram']); ?></a><?php endif; ?>
          <a class="rzx-btn rzx-btn--wide" href="<?php echo esc_url(rzx_url()); ?>">All exhibitors</a>
        </aside>
      </div>

      <?php $rel = rzx_related($e); if ($rel): ?>
      <section class="rzx-more">
        <div class="rzx-lab"><h2>More tables like this one</h2><i></i></div>
        <div class="rzx-grid rzx-grid--more"><?php foreach ($rel as $x) echo rzx_card($x); ?></div>
      </section>
      <?php endif; ?>

      <div class="rzx-sheet" data-sheet hidden>
        <div class="rzx-sheetin" role="dialog" aria-label="Share this profile">
          <b class="rzx-sheetttl">Share this profile</b>
          <p class="rzx-sheetsub">Send <?php echo rzx_h($e['name']); ?>&rsquo;s page to your followers.</p>
          <div class="rzx-sheetbtns">
            <button class="rzx-btn rzx-btn--gold rzx-btn--wide" type="button" data-sh="ig">Copy caption for Instagram</button>
            <button class="rzx-btn rzx-btn--wide" type="button" data-sh="x">Share to X</button>
            <button class="rzx-btn rzx-btn--wide" type="button" data-sh="fb">Share to Facebook</button>
            <button class="rzx-btn rzx-btn--wide" type="button" data-sh="link">Copy link</button>
            <button class="rzx-btn rzx-btn--wide" type="button" data-sh="native" hidden>More sharing options</button>
          </div>
          <div class="rzx-toast" data-sheettoast aria-live="polite"></div>
          <button class="rzx-sheetx" type="button" data-sheetclose aria-label="Close">&times;</button>
        </div>
      </div>

      <p class="rzx-prov"><b>How this profile was made.</b> Compiled by RazMania from <?php echo rzx_h($e['name']); ?>&rsquo;s exhibitor registration and <?php echo $e['instagram'] ? 'their public Instagram' : 'publicly available information about their business'; ?>, and reviewed before publishing. Prices, where shown, are what the exhibitor last posted publicly and are not a quote.</p>
    </article>
    <?php return ob_get_clean();
}

/* -------------------------------------------------------------------- best */

function rzx_render_best() {
    $r = rzx_rankings();
    $past = rzx_past();
    $by_id = [];
    foreach (rzx_all() as $x) $by_id[$x['id']] = $x;
    ob_start(); ?>
    <article class="rzx-page rzx-best">
      <nav class="rzx-crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(rzx_url()); ?>">Exhibitors</a> <span>/</span> <span aria-current="page">The Best of <?php echo rzx_h(rzx_event_name()); ?></span></nav>
      <header class="rzx-mast">
        <span class="rzx-eyebrow"><?php echo rzx_h(rzx_event_name()); ?> &middot; <?php echo count($r['lists']); ?> lists &middot; ranked ten deep</span>
        <h1 class="rzx-h1">The Best of <?php echo rzx_h(rzx_event_name()); ?></h1>
        <p class="rzx-dek">The tables collectors <?php echo $past ? 'should have hit' : 'should hit'; ?> first, one list per way of collecting. The top five in each list carry the <b>Best of <?php echo rzx_h(rzx_event_name()); ?></b> mark on their profile; six through ten are <b>RazMania Certified</b>.</p>
        <nav class="rzx-toc" aria-label="Lists">
          <?php foreach ($r['lists'] as $l): ?><a href="#<?php echo esc_attr($l['key']); ?>"><?php echo rzx_h($l['title']); ?></a><?php endforeach; ?>
        </nav>
      </header>

      <?php foreach ($r['lists'] as $l): ?>
      <section class="rzx-list" id="<?php echo esc_attr($l['key']); ?>">
        <div class="rzx-list-head">
          <div><span class="rzx-eyebrow"><?php echo rzx_h($l['strapline']); ?></span>
            <h2 class="rzx-list-title"><?php echo rzx_h($l['title']); ?></h2>
            <p class="rzx-list-who"><?php echo rzx_h($l['who']); ?> <?php echo (int)$l['field']; ?> tables in the field.</p></div>
        </div>
        <ol class="rzx-rank">
          <?php foreach ($l['entries'] as $x): $e = $by_id[$x['id']] ?? null; $best = $x['tier'] === 'best'; ?>
          <li class="rzx-rank-item <?php echo $best ? 'is-best' : 'is-cert'; ?>">
            <a href="<?php echo esc_url(home_url('/exhibitors/' . $x['slug'] . '/')); ?>">
              <span class="rzx-rank-num">#<?php echo (int)$x['rank']; ?></span>
              <?php echo $e ? rzx_logo($e, 'rzx-rank-logo') : ''; ?>
              <span class="rzx-rank-body">
                <span class="rzx-rank-name"><?php echo rzx_h($x['name']); ?><span class="rzx-badge <?php echo $best ? 'rzx-badge--best' : 'rzx-badge--cert'; ?>"><?php echo $best ? '&#9733; Best' : '&#10003; Certified'; ?></span></span>
                <?php if ($x['tagline']): ?><span class="rzx-rank-line"><?php echo rzx_h($x['tagline']); ?></span><?php endif; ?>
                <span class="rzx-rank-meta"><?php echo $x['location'] ? rzx_h($x['location']) . ' &middot; ' : ''; ?><?php echo $x['followers'] ? number_format((int)$x['followers']) . ' followers' : 'audience not on record'; ?></span>
              </span>
              <span class="rzx-rank-cta">Profile &rarr;</span>
            </a>
          </li>
          <?php endforeach; ?>
        </ol>
      </section>
      <?php endforeach; ?>

      <section class="rzx-box rzx-ed rzx-method">
        <div class="rzx-lab"><h2>How these lists are made</h2><i></i></div>
        <p class="lead"><?php echo rzx_h($r['method']); ?></p>
        <p>The audience signal is thin for the moment: only <?php echo count(array_filter(rzx_all(), function ($x) { return !empty($x['ig_followers']); })); ?> of <?php echo count(rzx_all()); ?> tables have a follower count on record, so the lower half of a list leans on profile completeness. As profile page views and the collectors' vote come online, they join the method here first.</p>
        <p class="rzx-byline">Exhibiting with us and think your table is placed wrong? Reply to any RazMania email with your Instagram handle and what you carry, and the next rebuild picks it up.</p>
      </section>
    </article>
    <?php return ob_get_clean();
}

/* ---------------------------------------------------------------- verified */

function rzx_render_verified() {
    $past = rzx_past();
    ob_start(); ?>
    <article class="rzx-page rzx-verified">
      <nav class="rzx-crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(rzx_url()); ?>">Exhibitors</a> <span>/</span> <span aria-current="page">Verified Exhibitor</span></nav>
      <header class="rzx-mast rzx-mast--stamp">
        <?php echo rzx_stamp(); ?>
        <div>
          <span class="rzx-eyebrow">RazMania Verified Exhibitor</span>
          <h1 class="rzx-h1">What this stamp means</h1>
          <p class="rzx-dek">Every business showing this stamp <?php echo $past ? 'had' : 'has'; ?> a confirmed table at <?php echo rzx_h(rzx_event_name()); ?>, and a profile page that RazMania built and checked before it went live. It cannot be bought.</p>
        </div>
      </header>
      <ol class="rzx-steps">
        <li><h2>Confirmed exhibitor</h2><p>They registered and booked a table for <?php echo rzx_h(rzx_event_name()); ?>, <?php echo rzx_h(rzx_event_dates()); ?> at the <?php echo rzx_h(rzx_venue()); ?> in Pontiac, Michigan. They <?php echo $past ? 'were' : 'are'; ?> on the floor plan, not a rumour.</p></li>
        <li><h2>Identity checked</h2><p>The business name, social accounts and shop links on their profile were matched against the details they gave us at registration. Where an owner listed a personal account, we linked the business account instead, or nothing at all.</p></li>
        <li><h2>Profile reviewed</h2><p>Everything written on their page comes from their own registration and their own public posts. A person at RazMania read it before it published. Nothing is invented, and prices shown are what the exhibitor last posted publicly.</p></li>
        <li><h2>Not for sale</h2><p>The stamp is not a sponsorship tier and no exhibitor paid for it. Every confirmed exhibitor gets one, which is the point: it tells you the table is real.</p></li>
      </ol>
      <section class="rzx-box rzx-ed">
        <div class="rzx-lab"><h2>What it does not mean</h2><i></i></div>
        <p class="lead">RazMania is not a party to any sale made at the show. The stamp is not a guarantee of any item, price, grade or condition, and it is not an endorsement of individual products. Card buying is still card buying: inspect what you are buying, and ask questions at the table.</p>
      </section>
      <div class="rzx-ctas rzx-ctas--center">
        <a class="rzx-btn rzx-btn--gold" href="<?php echo esc_url(rzx_url()); ?>">See every verified exhibitor</a>
        <?php echo rzx_ticket_btn(); ?>
      </div>
    </article>
    <?php return ob_get_clean();
}
